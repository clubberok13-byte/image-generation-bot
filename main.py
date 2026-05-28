"""
Image Generation Bot
- Слушает RSS-ленты двух Telegram-каналов
- Берёт промт из <code>/<blockquote> в новом посте
- Чередует референс-фото моделей (model_1 / model_2)
- Генерирует картинку через Nano Banana (Gemini 2.5 Flash Image)
- Постит в целевой канал + оригинальный промт в обсуждение
"""

import os
import io
import json
import html as html_module
import asyncio
import logging
from pathlib import Path
from typing import Optional

import feedparser
from bs4 import BeautifulSoup
from PIL import Image

from google import genai

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

STATE_FILE = Path("/tmp/seen.json")

CAPTION = (
    "Текст можно менять под себя, позы, цвет одежды, прически и т.д 🙌🏻\n\n"
    f'1) Заходим в бот <a href="{REFERENCE_LINK}">здесь</a> (Жмите на синюю надпись)\n'
    "2) Кнопка Дизайн с ИИ\n"
    "3) Кнопка Nano Banana\n"
    "4) Добавляете фото\n"
    "5) Добавляете Промт из комментариев."
)

# ──────────────────────────────────────────────────────────────────────
# Состояние
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
    Раскодируем экранированный HTML на случай двойного экранирования."""
    decoded = html_module.unescape(html_module.unescape(html))
    soup = BeautifulSoup(decoded, "html.parser")

    code = soup.find("code")
    if code:
        text = code.get_text(separator="\n").strip()
        if text:
            return text

    bq = soup.find("blockquote")
    if bq:
        text = bq.get_text(separator="\n").strip()
        if text:
            return text

    return None

def fetch_new_entries(seen_ids: list) -> list:
    """Возвращает новые посты со всех лент, от старого к новому."""
    fresh = []
    for feed_url in RSS_FEEDS:
        log.info("Опрашиваю RSS: %s", feed_url)
        try:
            d = feedparser.parse(feed_url)
        except Exception as e:
            log.exception("Не удалось распарсить ленту %s: %s", feed_url, e)
            continue

        log.info("Получено записей из ленты %s: %d", feed_url, len(d.entries))

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
                # для отладки: покажем первые 300 символов html этого поста
                log.info("Пост без промта (%s). HTML[:300]: %s", entry_id, html[:300])
                seen_ids.append(entry_id)
                continue

            log.info("Найден промт в посте %s", entry_id)
            fresh.append({
                "id": entry_id,
                "prompt": prompt,
                "link": entry.get("link", ""),
            })

    fresh.reverse()
    return fresh[-1:] if fresh else []

# ──────────────────────────────────────────────────────────────────────
# Nano Banana (Gemini 2.5 Flash Image)
# ──────────────────────────────────────────────────────────────────────

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def find_model_file(model_name: str) -> Path:
    exact = REF_PHOTOS_DIR / model_name
    if exact.exists() and exact.is_file():
        return exact
    for p in REF_PHOTOS_DIR.iterdir():
        if p.is_file() and p.stem == model_name:
            return p
    raise FileNotFoundError(f"Не найден файл референса для {model_name} в {REF_PHOTOS_DIR}")

def generate_image(prompt: str, model_name: str) -> bytes:
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

    await asyncio.sleep(4)
    try:
        chat = await bot.get_chat(TARGET_CHANNEL_ID)
        if chat.linked_chat_id:
            await bot.send_message(
                chat_id=chat.linked_chat_id,
                text=f"<b>Промт:</b>\n<pre>{prompt}</pre>",
                parse_mode=ParseMode.HTML,
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
                    state["seen_ids"] = state["seen_ids"][-500:]
                    save_state(state)
                    await asyncio.sleep(3)
        except Exception as e:
            log.exception("Ошибка в основном цикле: %s", e)

        log.info("Засыпаю на %s минут", POLL_INTERVAL_MINUTES)
        await asyncio.sleep(POLL_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    asyncio.run(main_loop())
