import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

load_dotenv("config/.env")
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "15"))

QUEUE_FILE = "queue.json"

async def can_post_now() -> bool:
    """Проверить можно ли отправлять сейчас (не превышен лимит)."""
    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    # Считаем посты за последние 24 часа
    now = datetime.fromisoformat(datetime.now().isoformat())
    posts_today = 0

    for item in queue_data["queue"]:
        if item["posted"] and item.get("posted_at"):
            posted_time = datetime.fromisoformat(item["posted_at"])
            if (now - posted_time).days < 1:  # Последние 24 часа
                posts_today += 1

    return posts_today < MAX_POSTS_PER_DAY

async def get_seconds_until_next_post() -> int:
    """Получить сколько секунд осталось до следующего поста."""
    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    if not queue_data.get("last_post_time"):
        return 0  # Можно постить сразу

    last_post = datetime.fromisoformat(queue_data["last_post_time"])
    now = datetime.now()

    # Интервал между постами (86400 сек в сутках / 15 постов = ~5760 сек = ~1.6 часа)
    interval_seconds = 86400 // MAX_POSTS_PER_DAY

    time_since_last = (now - last_post).total_seconds()
    seconds_to_wait = max(0, interval_seconds - time_since_last)

    return int(seconds_to_wait)

async def wait_until_can_post() -> None:
    """Ждать пока не можно будет постить (блокирующая функция)."""
    import asyncio

    while True:
        if await can_post_now():
            seconds = await get_seconds_until_next_post()
            if seconds == 0:
                return

            # Ждём
            await asyncio.sleep(seconds)
        else:
            # Лимит на день превышен, ждём до полуночи
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_to_midnight = (tomorrow - now).total_seconds()
            await asyncio.sleep(seconds_to_midnight)
