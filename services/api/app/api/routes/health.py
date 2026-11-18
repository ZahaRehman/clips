import asyncio
import subprocess

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter

from app.config import get_settings
from app.metrics import snapshot

router = APIRouter(tags=["health"])


def _ffmpeg_version() -> str:
    settings = get_settings()
    try:
        result = subprocess.run(
            [settings.ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return "missing"
        first = (result.stdout or "").splitlines()[0] if result.stdout else "missing"
        return first.strip() or "missing"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return "missing"


async def _redis_status() -> str:
    settings = get_settings()
    try:
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            pong = await redis.ping()
            return "ok" if pong else "error"
        finally:
            await redis.aclose()
    except Exception:
        return "error"


@router.get("/health")
async def health() -> dict[str, str | int]:
    ffmpeg, redis = await asyncio.gather(
        asyncio.to_thread(_ffmpeg_version),
        _redis_status(),
    )
    counts = snapshot()
    return {"status": "ok", "ffmpeg": ffmpeg, "redis": redis, **counts}
