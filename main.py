import os, io, json, base64, asyncio, logging
from pathlib import Path
from datetime import datetime
import pytz

import requests
from bs4 import BeautifulSoup
from PIL import Image
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("bot")

TELEGRAM_BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
REPLICATE_API_TOKEN  = os.environ["REPLICATE_API_TOKEN"]
TARGET_CHANNEL_ID    = int(os.environ["TARGET_CHANNEL_ID"])
REFERENCE_LINK       = os.environ["REFERENCE_LINK"]
POLL_INTERVAL        = 23

MOSCOW_TZ    = pytz.timezone("Europe/Moscow")
HOUR_START   = 7
HOUR_END     = 23

SOURCE_CHANNELS = ["IIFot", "gorbuzaksenia", "balahninaII"]
REPO_DIR        = Path(__file__).parent
REF_DIR         = REPO_DIR / "reference_photos"
MODELS          = ["model_1", "model_2"]
STATE_FILE      = Path("/tmp/seen.json")

TGME_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}


def is_active_hours():
    now = datetime.now(MOSCOW_TZ)
    return HOUR_START <= now.hour < HOUR_END


def make_caption(prompt):
    link = REFERENCE_LINK
    short_prompt = prompt[:800] + "..." if len(prompt) > 800 else prompt
    return (
        "Текст можно менять под себя, позы, цвет одежды, прически и т.д\n\n"
        "1) Заходим в бот " + '<a href="' + link + '">здесь</a>' + " (Жмите на синюю надпись)\n"
        "2) Кнопка Дизайн с ИИ\n"
        "3) Кнопка Nano Banana\n"
        "4) Добавляете фото\n"
        "5) Добавляете Промт из комментариев.\n\n"
        "<blockquote expandable>" + short_prompt + "</blockquote>"
    )


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"seen_ids": [], "rotation_index": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))


def extract_prompt(msg_div):
    code = msg_div.find("code")
    if code:
        t = code.get_text(separator="\n").strip()
        if t:
            return t
    bq = msg_div.find("blockquote")
    if bq:
        t = bq.get_text(separator="\n").strip()
        if t:
            return t
    return None


def fetch_new_entries(seen_ids):
    fresh = []
    for channel in SOURCE_CHANNELS:
        url = "https://t.me/s/" + channel
        log.info("Опрашиваю: %s", url)
        try:
            r = requests.get(url, headers=TGME_HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log.exception("Ошибка загрузки %s: %s", url, e)
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        msgs = soup.find_all("div", class_="tgme_widget_message")
        log.info("Найдено сообщений %s: %d", channel, len(msgs))
        for msg in msgs:
            pid = msg.get("data-post")
            if not pid:
                continue
            eid = "https://t.me/" + pid
            if eid in seen_ids:
                continue
            tdiv = msg.find("div", class_="tgme_widget_message_text")
            prompt = extract_prompt(tdiv) if tdiv else None
            if not prompt:
                seen_ids.append(eid)
                continue
            log.info("Найден промт в посте %s", eid)
            fresh.append({"id": eid, "prompt": prompt, "link": eid})
    return fresh[-1:] if fresh else []


def find_model_file(model_name):
    exact = REF_DIR / model_name
    if exact.exists() and exact.is_file():
        return exact
    for p in REF_DIR.iterdir():
        if p.is_file() and p.stem == model_name:
            return p
    raise FileNotFoundError("Не найден файл референса: " + model_name)


def generate_image(prompt, model_name):
    import replicate

    ref_path = find_model_file(model_name)
    log.info("Референс: %s", ref_path)

    img = Image.open(ref_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)

    client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    output = client.run(
        "black-forest-labs/flux-dev",
        input={
            "image": buf,
            "prompt": prompt,
            "prompt_strength": 0.75,
            "num_outputs": 1,
            "output_format": "jpg",
            "num_inference_steps": 28,
        }
    )

    image_url = str(list(output)[0])
    r = requests.get(image_url, timeout=60)
    r.raise_for_status()
    return r.content


async def post_to_channel(bot, image_bytes, prompt):
    caption = make_caption(prompt)
    bio = io.BytesIO(image_bytes)
    bio.seek(0)
    sent = await bot.send_photo(
        chat_id=TARGET_CHANNEL_ID,
        photo=bio,
        caption=caption,
        parse_mode=ParseMode.HTML,
    )
    log.info("Запостил картинку, message_id=%s", sent.message_id)


async def main_loop():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    me = await bot.get_me()
    log.info("Бот запущен: @%s", me.username)
    log.info("Каналы: %s", SOURCE_CHANNELS)
    log.info("Цель: %s", TARGET_CHANNEL_ID)

    while True:
        now = datetime.now(MOSCOW_TZ)
        if not is_active_hours():
            log.info("Сейчас %s МСК — вне рабочих часов, жду до 07:00", now.strftime("%H:%M"))
            await asyncio.sleep(60)
            continue

        state = load_state()
        try:
            new_posts = fetch_new_entries(state["seen_ids"])
            log.info("Новых постов: %d", len(new_posts))
            for post in new_posts:
                model = MODELS[state["rotation_index"] % len(MODELS)]
                log.info("Обрабатываю %s моделью %s", post["link"], model)
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
            log.exception("Ошибка в цикле: %s", e)

        log.info("Засыпаю на %d минут", POLL_INTERVAL)
        await asyncio.sleep(POLL_INTERVAL * 60)


if __name__ == "__main__":
    asyncio.run(main_loop())
