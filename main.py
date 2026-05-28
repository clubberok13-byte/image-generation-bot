"""
Image Generation Bot
- Слушает RSS-ленты двух Telegram-каналов
- Берёт промт из <code> в новом посте
- Чередует референс-фото моделей (model_1 / model_2)
- Генерирует картинку через Nano Banana (Gemini 2.5 Flash Image)
- Постит в целевой канал + оригинальный промт в обсуждение
"""

import os
import io
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional

import feedparser
from bs4 import BeautifulSoup
from PIL import Image

from google import genai
from google.genai import types as genai_types

from telegram import Bot, InputFile
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ──────────────────────────────────────────────────────────────────────
# Конфиг и логи
# ──────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
log = logging.getLogger("bot")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TARGET_CHANNEL_ID = int(os.environ["TARGET_CHANNEL_ID"])
RSS_FEEDS = [u.strip() for u in os.environ["RSS_FEEDS"].split(",") if u.strip()]
REFERENCE_LINK = os.environ["REFERENCE_LINK"]
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))

REPO_DIR = Path(__file__).parent
REF_PHOTOS_DIR = REPO_DIR / "reference_photos"
MODELS = ["model_1", "model_2"]

STATE_FILE = Path("/tmp/seen.json")  # сбрасывается при рестарте — ок для нашего случая

CAPTION = (
    "Текст можно менять под себя, позы, цвет одежды, прически и т.д 🙌🏻\n\n"
    f'1) Заходим в бот <a href="{REFERENCE_LINK}">здесь</a> (Жмите на синюю надпись)\n'
    "2) Кнопка Дизайн с ИИ\n"
    "3) Кнопка Nano Banana\n"
    "4) Добавляете фото\n"
    "5) Добавляете Промт из комментариев."
)

# ──────────────────────────────────────────────────────────────────────
# Состояние (что уже обработано)
# ──────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            log.warning("seen.json повреждён, начинаем с нуля")
    return {"seen_ids": [], "rotation_index": 0}

def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))

# ──────────────────────────────────────────────────────────────────────
# Парсинг RSS
# ──────────────────────────────────────────────────────────────────────

def extract_prompt(html: str) -> Optional[str]:
    """Достаёт текст промта из <code> или <blockquote>.
    RSSHub иногда отдаёт экранированный HTML, поэтому раскодируем дважды."""
    import html as html_module
    # раскодируем экранированные сущности (&lt; -> <) на случай двойного экранирования
    decoded = html_module.unescape(html)
    decoded = html_module.unescape(decoded)

    soup = BeautifulSoup(decoded, "html.parser")

    # 1) приоритет — <code>
    code = soup.find("code")
    if code:
        text = code.get_text(separator="\n").strip()
        if text:
            return text

    # 2) запасной вариант — <blockquote>
    bq = soup.find("blockquote")
    if bq:
        text = bq.get_text(separator="\n").strip()
        if text:
            return text
    return None

def fetch_new_entries(seen_ids: list[str]) -> list[dict]:
    """Возвращает новые посты со всех лент, в порядке от старого к новому."""
    fresh: list[dict] = []
    for feed_url in RSS_FEEDS:
        log.info("Опрашиваю RSS: %s", feed_url)
        try:
            d = feedparser.parse(feed_url)
        except Exception as e:
            log.exception("Не удалось распарсить ленту %s: %s", feed_url, e)
            continue
        for entry in d.entries:
            entry_id = entry.get("link") or entry.get("id")
            if not entry_id or entry_id in seen_ids:
                continue
            html = ""
            if entry.get("content"):
                html = entry["content"][0].get("value", "")
            elif entry.get("summary"):
                html = entry["summary"]
            prompt = extract_prompt(html)
            if not prompt:
                # помечаем как видели, чтоб не парсить снова, но не публикуем
                seen_ids.append(entry_id)
                continue
            fresh.append({
                "id": entry_id,
                "prompt": prompt,
                "link": entry.get("link", ""),
            })
    # самые старые сначала, чтобы публикация шла в естественном порядке
    fresh.reverse()
    return fresh[-1:] if fresh else []

# ──────────────────────────────────────────────────────────────────────
# Nano Banana (Gemini 2.5 Flash Image)
# ──────────────────────────────────────────────────────────────────────

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def find_model_file(model_name: str) -> Path:
    """Ищет файл модели — допускает любое расширение или без расширения."""
    # точное совпадение
    exact = REF_PHOTOS_DIR / model_name
    if exact.exists() and exact.is_file():
        return exact
    # с любым расширением
    for p in REF_PHOTOS_DIR.iterdir():
        if p.is_file() and p.stem == model_name:
            return p
    raise FileNotFoundError(f"Не найден файл референса для {model_name} в {REF_PHOTOS_DIR}")

def generate_image(prompt: str, model_name: str) -> bytes:
    """Возвращает байты сгенерированной картинки."""
    ref_path = find_model_file(model_name)
    log.info("Использую референс: %s", ref_path)
    ref_image = Image.open(ref_path)

    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[prompt, ref_image],
    )

    for part in response.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            return part.inline_data.data
    raise RuntimeError("Gemini не вернул изображение")

# ──────────────────────────────────────────────────────────────────────
# Постинг в Telegram
# ──────────────────────────────────────────────────────────────────────

async def post_to_channel(bot: Bot, image_bytes: bytes, prompt: str) -> None:
    bio = io.BytesIO(image_bytes)
    bio.name = "post.png"
    sent = await bot.send_photo(
        chat_id=TARGET_CHANNEL_ID,
        photo=InputFile(bio, filename="post.png"),
        caption=CAPTION,
        parse_mode=ParseMode.HTML,
    )
    log.info("Запостил картинку, message_id=%s", sent.message_id)

    # Попытка отправить промт в обсуждение (комментарии)
    # Группа обсуждений сама подцепляет пост и создаёт там тред.
    # Через 3–5 сек tg создаёт связку — можно попробовать ответить на forwarded copy.
    await asyncio.sleep(4)
    try:
        # Бот должен быть участником discussion-группы (или админом),
        # чтобы видеть автоматически созданный тред.
        # Telegram пересылает пост в discussion-группу с тем же message_id,
        # но нам проще отправить промт как отдельное сообщение в discussion-группу
        # с reply_to_message_id на автоматический форвард.
        # На практике для большинства каналов работает следующий хак:
        # forward_from_message_id в discussion группе == message_id в канале
        # Но API "send to comments" официально нет.
        # Поэтому делаем простой fallback: пробуем найти discussion chat.
        chat = await bot.get_chat(TARGET_CHANNEL_ID)
        if chat.linked_chat_id:
            await bot.send_message(
                chat_id=chat.linked_chat_id,
                text=f"<b>Промт:</b>\n<pre>{prompt}</pre>",
                parse_mode=ParseMode.HTML,
                reply_to_message_id=sent.message_id + 0,  # упрощённо — может не привязаться к посту
            )
            log.info("Отправил промт в discussion-группу")
    except TelegramError as e:
        log.warning("Не смог отправить промт в комментарии: %s", e)

# ──────────────────────────────────────────────────────────────────────
# Главный цикл
# ──────────────────────────────────────────────────────────────────────

async def main_loop():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    me = await bot.get_me()
    log.info("Бот запущен: @%s (id=%s)", me.username, me.id)
    log.info("Источники RSS: %s", RSS_FEEDS)
    log.info("Целевой канал: %s", TARGET_CHANNEL_ID)
    log.info("Модели: %s", MODELS)

    while True:
        state = load_state()
        try:
            new_posts = fetch_new_entries(state["seen_ids"])
            log.info("Новых постов с промтами: %d", len(new_posts))

            for post in new_posts:
                model = MODELS[state["rotation_index"] % len(MODELS)]
                log.info("Обрабатываю пост %s моделью %s", post["link"], model)
                try:
                    img_bytes = generate_image(post["prompt"], model)
                    await post_to_channel(bot, img_bytes, post["prompt"])
                    state["rotation_index"] += 1
                except Exception as e:
                    log.exception("Ошибка при обработке поста: %s", e)
                finally:
                    state["seen_ids"].append(post["id"])
                    # обрезаем историю чтобы не разрасталась
                    state["seen_ids"] = state["seen_ids"][-500:]
                    save_state(state)
                    await asyncio.sleep(3)  # маленькая пауза между постами
        except Exception as e:
            log.exception("Ошибка в основном цикле: %s", e)

        log.info("Засыпаю на %s минут", POLL_INTERVAL_MINUTES)
        await asyncio.sleep(POLL_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    asyncio.run(main_loop())
