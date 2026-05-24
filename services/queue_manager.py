import json
import os
from datetime import datetime
from pathlib import Path

QUEUE_FILE = "queue.json"

async def init_queue() -> None:
    """Инициализировать queue.json если его нет."""
    if not os.path.exists(QUEUE_FILE):
        initial_queue = {
            "queue": [],
            "daily_count": 0,
            "last_post_time": None,
            "next_post_in_seconds": 0,
            "last_channel": None
        }
        with open(QUEUE_FILE, 'w') as f:
            json.dump(initial_queue, f, indent=2)

async def add_to_queue(source: str, prompt: str) -> None:
    """Добавить промпт в очередь FIFO с чередованием каналов."""
    await init_queue()

    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    queue_item = {
        "id": f"{datetime.now().timestamp()}",
        "source": source,
        "prompt": prompt,
        "status": "pending",
        "added_at": datetime.now().isoformat(),
        "generated_image": None,
        "posted": False
    }

    queue_data["queue"].append(queue_item)

    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f, indent=2)

async def get_next_from_queue() -> dict | None:
    """Вернуть следующий промпт из очереди (первый незавершённый)."""
    await init_queue()

    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    for item in queue_data["queue"]:
        if item["status"] == "pending" and not item["posted"]:
            return item

    return None

async def mark_as_generating(queue_id: str) -> None:
    """Отметить что начали генерировать."""
    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    for item in queue_data["queue"]:
        if item["id"] == queue_id:
            item["status"] = "generating"
            break

    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f, indent=2)

async def mark_as_generated(queue_id: str, image_path: str) -> None:
    """Отметить что сгенерировали фото."""
    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    for item in queue_data["queue"]:
        if item["id"] == queue_id:
            item["status"] = "generated"
            item["generated_image"] = image_path
            break

    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f, indent=2)

async def mark_as_posted(queue_id: str) -> None:
    """Отметить что отправили в канал."""
    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    for item in queue_data["queue"]:
        if item["id"] == queue_id:
            item["posted"] = True
            item["status"] = "posted"
            item["posted_at"] = datetime.now().isoformat()
            break

    queue_data["daily_count"] = sum(1 for item in queue_data["queue"] if item["posted"])
    queue_data["last_post_time"] = datetime.now().isoformat()

    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f, indent=2)

async def get_queue_length() -> int:
    """Получить количество непостленных элементов в очереди."""
    await init_queue()

    with open(QUEUE_FILE, 'r') as f:
        queue_data = json.load(f)

    return sum(1 for item in queue_data["queue"] if not item["posted"])
