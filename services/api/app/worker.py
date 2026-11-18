import asyncio
from datetime import datetime, timedelta, timezone

from arq.connections import RedisSettings
from arq.cron import cron

from app.config import get_settings
from app.logging_setup import configure_logging, get_logger
from app.pipeline import runner
from app.pipeline.cleanup import run_cleanup
from app.storage import get_storage
from app.tiktok.upload import execute_tiktok_publish, unfinished_tiktok_ids
from app.worker_queues import PUBLISH_QUEUE, RENDER_QUEUE
from app.youtube.upload import execute_publish, unfinished_publish_ids


async def startup(ctx: dict) -> None:
    configure_logging()


async def publish_startup(ctx: dict) -> None:
    """Resume uploads the last run left unfinished, on the publish queue."""
    configure_logging()
    publish_ids = await asyncio.to_thread(unfinished_publish_ids)
    for publish_id in publish_ids:
        await ctx["redis"].enqueue_job("run_publish", publish_id, _queue_name=PUBLISH_QUEUE)
    tiktok_ids = await asyncio.to_thread(unfinished_tiktok_ids)
    for publish_id in tiktok_ids:
        await ctx["redis"].enqueue_job("run_tiktok_publish", publish_id, _queue_name=PUBLISH_QUEUE)


async def ping(ctx: dict) -> str:
    get_storage().put(
        "_worker_ping.txt",
        (datetime.now(timezone.utc).isoformat() + "\n").encode(),
    )
    return "pong"


async def run_pipeline(ctx: dict, project_id: str, job_id: str, from_stage: str = "ingest") -> str:
    return await asyncio.to_thread(runner.run_pipeline, project_id, job_id, from_stage)


async def run_export(ctx: dict, clip_id: str, export_id: str, job_id: str) -> str:
    return await asyncio.to_thread(runner.run_export, clip_id, export_id, job_id)


async def run_metadata(ctx: dict, project_id: str, job_id: str) -> str:
    return await asyncio.to_thread(runner.run_metadata, project_id, job_id)


async def run_publish(ctx: dict, publish_id: str) -> str:
    try:
        outcome = await asyncio.to_thread(execute_publish, publish_id)
    except Exception:
        get_logger().error("publish failed", extra={"publish_id": publish_id, "error_code": "PUBLISH_FAILED", "stage": "publish"})
        return "failed"
    if outcome == "retry":
        await ctx["redis"].enqueue_job(
            "run_publish", publish_id, _defer_by=timedelta(seconds=30), _queue_name=PUBLISH_QUEUE
        )
    elif outcome == "poll_again":
        await ctx["redis"].enqueue_job(
            "run_publish", publish_id, _defer_by=timedelta(seconds=45), _queue_name=PUBLISH_QUEUE
        )
    return outcome


async def run_tiktok_publish(ctx: dict, publish_id: str) -> str:
    try:
        outcome = await asyncio.to_thread(execute_tiktok_publish, publish_id)
    except Exception:
        get_logger().error(
            "tiktok publish failed",
            extra={"publish_id": publish_id, "error_code": "TIKTOK_PUBLISH_FAILED", "stage": "tiktok"},
        )
        return "failed"
    if outcome == "retry":
        await ctx["redis"].enqueue_job(
            "run_tiktok_publish", publish_id, _defer_by=timedelta(seconds=30), _queue_name=PUBLISH_QUEUE
        )
    elif outcome == "poll_again":
        await ctx["redis"].enqueue_job(
            "run_tiktok_publish", publish_id, _defer_by=timedelta(seconds=45), _queue_name=PUBLISH_QUEUE
        )
    return outcome


async def cleanup_job(ctx: dict) -> str:
    stats = await asyncio.to_thread(run_cleanup)
    return str(stats["bytes_reclaimed"])


class WorkerSettings:
    functions = [ping, run_pipeline, run_export, run_metadata, run_publish, run_tiktok_publish, cleanup_job]
    cron_jobs = [cron(cleanup_job, hour={3, 15}, minute=15)]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 1
    job_timeout = get_settings().worker_job_timeout_sec


async def render_startup(ctx: dict) -> None:
    configure_logging()


class RenderSettings:
    """`arq app.worker.RenderSettings`: user exports only, one at a time."""

    functions = [run_export]
    on_startup = render_startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = RENDER_QUEUE
    max_jobs = 1
    job_timeout = get_settings().worker_job_timeout_sec


class PublishSettings:
    """`arq app.worker.PublishSettings`: YouTube and TikTok uploads, beside clipping."""

    functions = [run_publish, run_tiktok_publish]
    on_startup = publish_startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = PUBLISH_QUEUE
    # One at a time: both uploaders refresh and rewrite the same token file.
    max_jobs = 1
    job_timeout = get_settings().worker_job_timeout_sec
