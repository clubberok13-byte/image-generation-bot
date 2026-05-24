from aiogram import Bot
from aiogram.types import Chat
from dotenv import load_dotenv
import os
import logging
from services.queue_manager import add_to_queue

load_dotenv("config/.env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SOURCE_CHANNEL_1 = os.getenv("SOURCE_CHANNEL_1", "IIFot")
SOURCE_CHANNEL_2 = os.getenv("SOURCE_CHANNEL_2", "gorbuzaksenia")

logger = logging.getLogger(__name__)

async def listen_to_channels(bot: Bot) -> None:
    """Слушать обновления из каналов (использует polling).

    Это функция для polling режима. Она будет вызваться в main.py
    """
    logger.info(f"Listening to channels: @{SOURCE_CHANNEL_1}, @{SOURCE_CHANNEL_2}")

async def process_channel_message(message: str, channel_name: str) -> None:
    """Обработать сообщение из канала - добавить в очередь.

    Args:
        message: текст сообщения (промпт)
        channel_name: имя канала (@IIFot или @gorbuzaksenia)
    """
    if not message or not message.strip():
        return

    source = f"@{channel_name}" if not channel_name.startswith("@") else channel_name

    await add_to_queue(source=source, prompt=message)

    logger.info(f"Added to queue from {source}: {message[:50]}...")

# Обработчик для aiogram (будет использоваться в main.py)
async def handle_channel_update(update_data: dict) -> None:
    """Обработчик обновлений от Telegram (для интеграции с диспетчером).

    Эта функция будет вызваться когда приходит сообщение из подписанного канала.
    """
    if "message" in update_data:
        message = update_data["message"]
        text = message.get("text", "")

        if "chat" in message:
            chat = message["chat"]
            channel_name = chat.get("username", "unknown")

            await process_channel_message(text, channel_name)
