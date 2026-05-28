"""
Image Generation Bot — парсинг t.me/s/ + Nano Banana (прямой REST)
- Качает веб-превью каналов (https://t.me/s/<channel>)
- Берёт промт из <code>/<blockquote> в новых постах
- Чередует референс-фото моделей (model_1 / model_2)
- Генерирует картинку через Gemini 2.5 Flash Image (прямой REST-вызов)
- Постит в целевой канал + промт в обсуждение
"""

import os
import io
import json
import base64
import asyncio
import logging
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from PIL import Image

from telegram import Bot
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
REFERENCE_LINK = os.environ["REFERENCE_LINK"]
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))

SOURCE_CHANNELS = ["IIFot", "gorbuzaksenia"]

REPO_DIR = Path(__file__).parent
REF_PHOTOS_DIR = REPO_DIR / "reference_photos"
MODELS = ["model_1", "model_2"]

STATE_FILE = Path("/tmp/seen.json")

GEMINI_MODEL = "gemini-2.5-flash-image"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}

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
# Парсинг t.me/s/
# ──────────────────────────────────────────────────────────────────────

def extract_prompt_from_message(msg_div) -> Optional[str]:
    code = msg_div.find("code")
    if code:
        text = code.get_text(separator="\n").strip()
        if text:
            return text
    bq = msg_div.find("blockquote")
    if bq:
        text = bq.get_text(separator="\n").strip()
        if text:
            return text
    return None

def fetch_new_entries(seen_ids: list) -> list:
    fresh = []
    for channel in SOURCE_CHANNELS:
        url = f"https://t.me/s/{channel}"
        log.info("Опрашиваю канал: %s", url)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            log.exception("Не удалось загрузить %s: %s", url, e)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        messages = soup.find_all("div", class_="tgme_widget_message")
        log.info("Найдено сообщений на странице %s: %d", channel, len(messages))

        for msg in messages:
            post_id = msg.get("data-post")
            if not post_id:
                continue
            entry_id = f"https://t.me/{post_id}"
            if entry_id in seen_ids:
                continue

            text_div = msg.find("div", class_="tgme_widget_message_text")
            prompt = extract_prompt_from_message(text_div) if text_div else None

            if not prompt:
                seen_ids.append(entry_id)
                continue

            log.info("Найден промт в посте %s", entry_id)
            fresh.append({
                "id": entry_id,
                "prompt": prompt,
                "link": entry_id,
            })

    return fresh[-1:] if fresh else []

# ──────────────────────────────────────────────────────────────────────
# Nano Banana (Gemini 2.5 Flash Image) — прямой REST через requests
# ──────────────────────────────────────────────────────────────────────

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

    # читаем референс и кодируем в base64 (JPEG)
    img = Image.open(ref_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    # тело запроса — обычный JSON, requests отправит его как UTF-8
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": "image/jpeg", "data": img_b64}},
            ]
        }]
    }
    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
    }

    resp = requests.post(GEMINI_API_URL, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini вернул {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Неожиданный ответ Gemini: {json.dumps(data)[:300]}")

    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            raw = base64.b64decode(inline["data"])
            out_img = Image.open(io.BytesIO(raw))
            if out_img.mode != "RGB":
                out_img = out_img.convert("RGB")
            out = io.BytesIO()
            out_img.save(out, format="PNG")
            out.seek(0)
            return out.read()

    raise RuntimeError("Gemini не вернул изображение")

# ──────────────────────────────────────────────────────────────────────
# Постинг в Telegram
# ──────────────────────────────────────────────────────────────────────

async def post_to_channel(bot: Bot, image_bytes: bytes, prompt: str) -> None:
    bio = io.BytesIO(image_bytes)
    bio.seek(0)
    sent = await bot.send_photo(
        chat_id=TARGET_CHANNEL_ID,
        photo=bio,
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
    log.info("Каналы-источники: %s", SOURCE_CHANNELS)
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
    asyncio.run(main_loop())    f'1) Заходим в бот <a href="{REFERENCE_LINK}">здесь</a> (Жмите на синюю надпись)\n'
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
# Парсинг t.me/s/
# ──────────────────────────────────────────────────────────────────────

def extract_prompt_from_message(msg_div) -> Optional[str]:
    code = msg_div.find("code")
    if code:
        text = code.get_text(separator="\n").strip()
        if text:
            return text
    bq = msg_div.find("blockquote")
    if bq:
        text = bq.get_text(separator="\n").strip()
        if text:
            return text
    return None

def fetch_new_entries(seen_ids: list) -> list:
    fresh = []
    for channel in SOURCE_CHANNELS:
        url = f"https://t.me/s/{channel}"
        log.info("Опрашиваю канал: %s", url)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            log.exception("Не удалось загрузить %s: %s", url, e)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        messages = soup.find_all("div", class_="tgme_widget_message")
        log.info("Найдено сообщений на странице %s: %d", channel, len(messages))

        for msg in messages:
            post_id = msg.get("data-post")
            if not post_id:
                continue
            entry_id = f"https://t.me/{post_id}"
            if entry_id in seen_ids:
                continue

            text_div = msg.find("div", class_="tgme_widget_message_text")
            prompt = extract_prompt_from_message(text_div) if text_div else None

            if not prompt:
                seen_ids.append(entry_id)
                continue

            log.info("Найден промт в посте %s", entry_id)
            fresh.append({
                "id": entry_id,
                "prompt": prompt,
                "link": entry_id,
            })

    return fresh[-1:] if fresh else []

# ──────────────────────────────────────────────────────────────────────
# Gemini (Nano Banana)
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
            raw = part.inline_data.data
            # перекодируем в чистый PNG через PIL, чтобы избежать проблем
            img = Image.open(io.BytesIO(raw))
            if img.mode != "RGB":
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="PNG")
            out.seek(0)
            return out.read()
    raise RuntimeError("Gemini не вернул изображение")

# ──────────────────────────────────────────────────────────────────────
# Постинг в Telegram
# ──────────────────────────────────────────────────────────────────────

async def post_to_channel(bot: Bot, image_bytes: bytes, prompt: str) -> None:
    bio = io.BytesIO(image_bytes)
    bio.seek(0)
    sent = await bot.send_photo(
        chat_id=TARGET_CHANNEL_ID,
        photo=bio,
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
    log.info("Каналы-источники: %s", SOURCE_CHANNELS)
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
