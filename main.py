import asyncio
import logging
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from dotenv import load_dotenv
import os

# Загружаем переменные окружения
load_dotenv("config/.env")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загружаем токены и Chat IDs
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SOURCE_CHANNEL_1_ID = int(os.getenv("SOURCE_CHANNEL_1_ID"))
SOURCE_CHANNEL_2_ID = int(os.getenv("SOURCE_CHANNEL_2_ID"))
SOURCE_CHANNEL_1_NAME = os.getenv("SOURCE_CHANNEL_1_NAME", "IIFot")
SOURCE_CHANNEL_2_NAME = os.getenv("SOURCE_CHANNEL_2_NAME", "gorbuzaksenia")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID"))

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN должен быть в config/.env")

async def channel_message_handler(message: Message, channel_name: str) -> None:
    """Обработчик сообщений из каналов."""
    from services.queue_manager import add_to_queue

    if not message.text or message.text.strip() == "":
        return

    prompt = message.text
    source = f"@{channel_name}"

    await add_to_queue(source=source, prompt=prompt)
    logger.info(f"Added to queue from {source}: {prompt[:50]}...")

async def main() -> None:
    """Главная функция запуска бота."""

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    router = Router()

    @router.channel_post()
    async def process_channel_posts(message: Message) -> None:
        """Обработчик постов из каналов по Chat ID."""
        if message.chat.id == SOURCE_CHANNEL_1_ID:
            await channel_message_handler(message, SOURCE_CHANNEL_1_NAME)
        elif message.chat.id == SOURCE_CHANNEL_2_ID:
            await channel_message_handler(message, SOURCE_CHANNEL_2_NAME)

    dp.include_router(router)

    logger.info("🤖 Image Generation Bot запущен!")
    logger.info(f"Слушаю каналы: @{SOURCE_CHANNEL_1_NAME} (ID: {SOURCE_CHANNEL_1_ID}), @{SOURCE_CHANNEL_2_NAME} (ID: {SOURCE_CHANNEL_2_ID})")
    logger.info(f"Публикую в: ID {TARGET_CHANNEL_ID}")
    logger.info(f"Rate limit: макс {os.getenv('MAX_POSTS_PER_DAY', '15')} постов в день")

    from services.post_manager import post_processing_loop

    processing_task = asyncio.create_task(post_processing_loop(bot))

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        processing_task.cancel()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
