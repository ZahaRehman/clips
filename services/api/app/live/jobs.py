"""Persist a closed window and enqueue the existing pipeline for that file only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Clip, Job, LiveWindow, Project
from app.pipeline.paths import rel_to_data

RESUME_MESSAGE = (
    "capture resumed at the live edge; the gap while the box was down was not recorded"
)
NO_WINDOWS_MESSAGE = "No windows were captured."


def next_index(session: Session, project_id: str) -> int:
    current = session.query(LiveWindow.index).filter(LiveWindow.project_id == project_id).all()
    if not current:
        return 1
    return max(int(row[0]) for row in current) + 1


def stream_origin_from_windows(session: Session, project_id: str) -> float:
    rows = session.query(LiveWindow.stream_end_sec).filter(LiveWindow.project_id == project_id).all()
    if not rows:
        return 0.0
    return max(float(row[0]) for row in rows)


def register_window(
    session: Session,
    project: Project,
    *,
    index: int,
    stream_start: float,
    stream_end: float,
    path: Path,
) -> LiveWindow:
    rel = rel_to_data(path)
    if Path(rel).name in {"follow.mp4", "follow.mp4.part"} or rel.endswith(".part"):
        raise AppError("INGEST_FAILED", "Refusing to enqueue the live follow file", status_code=500)
    window = LiveWindow(
        project_id=project.id,
        index=index,
        stream_start_sec=round(stream_start, 3),
        stream_end_sec=round(stream_end, 3),
        path=rel,
        status="queued",
    )
    session.add(window)
    session.commit()
    session.refresh(window)
    return window


def queue_window_job(session: Session, project: Project, window: LiveWindow) -> Job:
    job = Job(
        project_id=project.id,
        kind="pipeline",
        status="queued",
        stage="queued",
        progress=0.0,
        checkpoint={
            "live_window": {
                "id": window.id,
                "index": window.index,
                "path": window.path,
                "stream_start_sec": window.stream_start_sec,
                "stream_end_sec": window.stream_end_sec,
            }
        },
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    try:
        enqueue_sync("run_pipeline", project.id, job.id, "ingest")
    except Exception:
        window.status = "failed"
        window.error = "Could not queue the pipeline job."
        job.status = "failed"
        job.error = window.error
        session.add(window)
        session.add(job)
        session.commit()
    return job


def mark_window(session: Session, job: Job, status: str, error: str = "") -> None:
    live = (job.checkpoint or {}).get("live_window")
    if not isinstance(live, dict) or not live.get("id"):
        return
    window = session.get(LiveWindow, str(live["id"]))
    if not window:
        return
    window.status = status
    window.error = (error or "")[:2000] or None
    session.add(window)
    session.commit()


def finish_capture(
    session: Session,
    project: Project,
    *,
    state: str,
    message: str | None = None,
    error: str | None = None,
) -> None:
    """Stop the project status. Does not delete clips or window files."""
    try:
        session.refresh(project)
    except Exception:
        return
    has_clip = session.query(Clip.id).filter(Clip.project_id == project.id).first() is not None
    meta = dict(project.source_meta or {})
    meta["live_state"] = state
    if message:
        meta["live_message"] = message
    elif not has_clip and not meta.get("live_message"):
        meta["live_message"] = NO_WINDOWS_MESSAGE
    if error:
        meta["live_error"] = error[:2000]
    project.source_meta = meta
    if project.status == "live":
        project.status = "ready_for_review" if has_clip else "done"
    session.add(project)
    session.commit()


def remember_cut(session: Session, project: Project, *, last_cut: float, origin: float, behind: float) -> None:
    meta = dict(project.source_meta or {})
    meta["follow_last_cut"] = round(last_cut, 3)
    meta["stream_origin"] = round(origin, 3)
    meta["behind_sec"] = round(max(0.0, behind), 1)
    meta["live_state"] = "running"
    project.source_meta = meta
    session.add(project)
    session.commit()


def enqueue_sync(function: str, *args: str, queue: str | None = None) -> None:
    asyncio.run(_enqueue(function, *args, queue=queue))


async def _enqueue(function: str, *args: str, queue: str | None = None) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        kwargs = {"_queue_name": queue} if queue else {}
        await redis.enqueue_job(function, *args, **kwargs)
    finally:
        await redis.aclose()
