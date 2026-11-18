"""Arq process that only downloads the next finished-video range."""

import asyncio

from arq.connections import RedisSettings

from app.config import get_settings
from app.logging_setup import configure_logging
from app.vod.fetch import fetch_chunk
from app.vod.schedule import FETCH_QUEUE


async def startup(ctx: dict) -> None:
    configure_logging()


async def run_fetch_chunk(ctx: dict, project_id: str, index: str) -> str:
    return await asyncio.to_thread(fetch_chunk, project_id, int(index))


class FetcherSettings:
    functions = [run_fetch_chunk]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = FETCH_QUEUE
    max_jobs = 1
    job_timeout = get_settings().worker_job_timeout_sec
