import os
import random
from datetime import datetime
from pathlib import Path
from openai import AsyncOpenAI
from dotenv import load_dotenv
import aiohttp
import logging

load_dotenv("config/.env")

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REFERENCE_PHOTOS_DIR = os.getenv("REFERENCE_PHOTOS_DIR", "./reference_photos")
GENERATED_PHOTOS_DIR = os.getenv("GENERATED_PHOTOS_DIR", "./generated_photos")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def get_random_reference_photo() -> str | None:
    """Получить случайное reference фото из папки."""
    ref_path = Path(REFERENCE_PHOTOS_DIR)
    if not ref_path.exists():
        return None

    photos = list(ref_path.glob("*.jpg")) + list(ref_path.glob("*.png"))

    if not photos:
        return None

    return str(random.choice(photos))

async def generate_image(prompt: str, reference_photo_path: str | None = None) -> str | None:
    """Генерировать изображение через DALL-E 3 с reference фото.

    Args:
        prompt: промпт для генерации
        reference_photo_path: путь к reference фото (опционально)

    Returns:
        Путь к сохранённому изображению или None если ошибка
    """
    try:
        if not reference_photo_path:
            reference_photo_path = await get_random_reference_photo()

        enhanced_prompt = f"{prompt}. Professional, high quality, detailed, cinematic lighting."

        # Если есть reference фото — описываем его стиль через Vision и добавляем в промпт
        if reference_photo_path:
            style_description = await _describe_reference_style(reference_photo_path)
            if style_description:
                enhanced_prompt = f"{enhanced_prompt} Style reference: {style_description}"

        response = await client.images.generate(
            model="dall-e-3",
            prompt=enhanced_prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )

        image_url = response.data[0].url
        if not image_url:
            logger.error("DALL-E 3 вернул пустой URL")
            return None

        saved_path = await _download_and_save(image_url)
        return saved_path

    except Exception as e:
        logger.error("Ошибка генерации изображения: %s", e)
        return None

async def _describe_reference_style(photo_path: str) -> str | None:
    """Описать стиль reference фото через GPT-4o Vision для использования в промпте."""
    try:
        import base64
        with open(photo_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        ext = Path(photo_path).suffix.lower().lstrip(".")
        media_type = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

        vision_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_data}"},
                        },
                        {
                            "type": "text",
                            "text": "Describe the visual style, lighting, color palette, and composition of this image in 1-2 sentences for use as a style reference.",
                        },
                    ],
                }
            ],
            max_tokens=100,
        )

        return vision_response.choices[0].message.content
    except Exception as e:
        logger.warning("Не удалось описать reference фото: %s", e)
        return None

async def _download_and_save(image_url: str) -> str | None:
    """Скачать изображение по URL и сохранить в GENERATED_PHOTOS_DIR."""
    try:
        Path(GENERATED_PHOTOS_DIR).mkdir(parents=True, exist_ok=True)

        filename = f"generated_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        save_path = os.path.join(GENERATED_PHOTOS_DIR, filename)

        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    f.write(await resp.read())

        logger.info("Изображение сохранено: %s", save_path)
        return save_path

    except Exception as e:
        logger.error("Ошибка сохранения изображения: %s", e)
        return None
