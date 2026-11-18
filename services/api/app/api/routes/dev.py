from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/ping-worker")
async def ping_worker() -> dict[str, str]:
    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        job = await redis.enqueue_job("ping")
        job_id = job.job_id if job else ""
        return {"job_id": job_id, "status": "queued"}
    finally:
        await redis.aclose()
