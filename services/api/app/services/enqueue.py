from arq import create_pool
from arq.connections import RedisSettings

from app.config import get_settings


async def enqueue(function: str, *args: str, queue: str | None = None) -> None:
    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        kwargs = {"_queue_name": queue} if queue else {}
        await redis.enqueue_job(function, *args, **kwargs)
    finally:
        await redis.aclose()
