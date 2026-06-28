import os, io, asyncio
from pathlib import Path
from PIL import Image
from telegram import Bot
from telegram.constants import ParseMode
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FAL_KEY            = os.environ["FAL_KEY"]
TARGET_CHANNEL_ID  = int(os.environ["TARGET_CHANNEL_ID"])
REFERENCE_LINK     = os.environ["REFERENCE_LINK"]

os.environ["FAL_KEY"] = FAL_KEY

REPO_DIR = Path(__file__).parent.parent
REF_DIR  = REPO_DIR / "reference_photos"

TEST_PROMPT = (
    "Создай портрет молодой женщины с тёмными волосами, в красном вечернем платье, "
    "на фоне ночного города с огнями. Фотореалистичный стиль, профессиональное освещение."
)


def generate():
    import fal_client
    ref_path = next(p for p in REF_DIR.iterdir() if p.stem == "model_1")
    img = Image.open(ref_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    print("Загружаю референс на fal.ai CDN...")
    face_url = fal_client.upload(buf.read(), content_type="image/jpeg")
    print(f"CDN URL: {face_url}")
    result = fal_client.subscribe(
        "fal-ai/flux-pro/v1.1-ultra/redux",
        arguments={
            "image_url": face_url,
            "prompt": TEST_PROMPT,
            "image_prompt_strength": 0.4,
            "num_images": 1,
            "output_format": "jpeg",
            "aspect_ratio": "3:4",
        }
    )
    url = result["images"][0]["url"]
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


async def post(image_bytes):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    caption = (
        "Текст можно менять под себя, позы, цвет одежды, прически и т.д\n\n"
        '1) Заходим в бот <a href="' + REFERENCE_LINK + '">здесь</a> (Жмите на синюю надпись)\n'
        "2) Кнопка Дизайн с ИИ\n3) Кнопка Nano Banana\n4) Добавляете фото\n"
        "5) Добавляете Промт из комментариев.\n\n"
        "<blockquote expandable>" + TEST_PROMPT + "</blockquote>"
    )
    bio = io.BytesIO(image_bytes)
    bio.seek(0)
    sent = await bot.send_photo(chat_id=TARGET_CHANNEL_ID, photo=bio,
                                caption=caption, parse_mode=ParseMode.HTML)
    print(f"Отправлено! message_id={sent.message_id}")


if __name__ == "__main__":
    print("Генерирую тест-изображение через PuLID...")
    img = generate()
    print(f"Готово ({len(img)} байт). Постю в канал...")
    asyncio.run(post(img))
