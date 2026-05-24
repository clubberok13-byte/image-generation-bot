from aiogram import Bot
from pathlib import Path
from dotenv import load_dotenv
import os
import logging
from services.queue_manager import mark_as_posted

load_dotenv("config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID"))
BOT_LINK = "https://t.me/syntxaibot?start=aff_6898253887"

logger = logging.getLogger(__name__)

async def post_to_channel(
    queue_id: str,
    image_path: str,
    original_prompt: str,
    source_channel: str
) -> bool:
    """Отправить фото в TARGET_CHANNEL_ID с подписью и инструкциями."""
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        if not Path(image_path).exists():
            logger.error(f"Image file not found: {image_path}")
            return False

        caption = f"""📸 Промпт: {original_prompt}

Текст можно менять под себя, позы, цвет одежды, прически и т.д 🙌🏻

1️⃣ Заходим в бот (Жмите на синюю надпись)
2️⃣ Кнопка Дизайн с ИИ
3️⃣ Кнопка Nano Banana
4️⃣ Добавляете фото
5️⃣ Добавляете Промт из комментариев

👉 {BOT_LINK}
"""

        with open(image_path, 'rb') as photo:
            await bot.send_photo(
                chat_id=TARGET_CHANNEL_ID,
                photo=photo,
                caption=caption,
                parse_mode="HTML"
            )

        logger.info(f"Posted to channel {TARGET_CHANNEL_ID}: {original_prompt[:50]}...")

        await mark_as_posted(queue_id)

        await bot.session.close()
        return True

    except Exception as e:
        logger.error(f"Error posting to channel: {str(e)}")
        return False

async def post_processing_loop(bot: Bot) -> None:
    """Основной цикл обработки очереди и постинга."""
    import asyncio
    from services.queue_manager import get_next_from_queue, mark_as_generating, mark_as_generated
    from services.image_generator import generate_image
    from services.rate_limiter import wait_until_can_post

    logger.info("Post processing loop started")

    while True:
        try:
            queue_item = await get_next_from_queue()

            if not queue_item:
                await asyncio.sleep(30)
                continue

            queue_id = queue_item["id"]
            prompt = queue_item["prompt"]
            source = queue_item["source"]

            await mark_as_generating(queue_id)
            logger.info(f"Generating image for: {prompt[:50]}...")

            image_path = await generate_image(prompt)

            if not image_path:
                logger.error(f"Failed to generate image for: {prompt}")
                continue

            await mark_as_generated(queue_id, image_path)
            logger.info(f"Image generated: {image_path}")

            logger.info("Waiting for rate limit to allow posting...")
            await wait_until_can_post()

            success = await post_to_channel(
                queue_id=queue_id,
                image_path=image_path,
                original_prompt=prompt,
                source_channel=source
            )

            if success:
                logger.info(f"Successfully posted to channel")
            else:
                logger.error(f"Failed to post to channel")

        except Exception as e:
            logger.error(f"Error in post processing loop: {str(e)}")
            await asyncio.sleep(10)
