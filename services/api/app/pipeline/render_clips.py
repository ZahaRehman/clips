"""render_clips stage: autopilot renders every kept clip without an Accept click."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.errors import AppError
from app.logging_setup import get_logger
from app.models import Clip, ClipExport, Job, Project
from app.pipeline.export import PRESET, render_export
from app.pipeline.job_state import update_job


# Rendering is the long tail after propose_windows (0.95). Keep the bar moving inside this band.
RENDER_PROGRESS_START = 0.96
RENDER_PROGRESS_END = 0.99


def render_progress(fraction: float) -> float:
    fraction = min(1.0, max(0.0, fraction))
    return RENDER_PROGRESS_START + (RENDER_PROGRESS_END - RENDER_PROGRESS_START) * fraction


def run_render_clips(session: Session, project: Project, job: Job) -> dict[str, Any]:
    """Render clips in rank order. One failed clip is recorded and skipped; the stage keeps going."""
    if not project.autopilot:
        raise AppError("RENDER_NOT_ALLOWED", "Only autopilot projects render without Accept", status_code=400)
    clips = [c for c in _kept_clips(session, project.id) if not _has_ready_export(session, c.id)]
    live = (job.checkpoint or {}).get("live_window")
    if isinstance(live, dict) and live.get("stream_start_sec") is not None:
        offset = float(live["stream_start_sec"])
        clips = [clip for clip in clips if clip.stream_offset_sec == offset]
    total = len(clips)
    summary: dict[str, Any] = {"total": total, "current": 0, "rendered": 0, "failed": 0, "errors": []}
    _progress(session, job, summary)

    for index, clip in enumerate(clips):
        summary["current"] = index + 1
        _progress(session, job, summary, index / total)
        export = ClipExport(clip_id=clip.id, preset=PRESET, status="pending")
        session.add(export)
        session.commit()
        try:
            render_export(session, clip, export, force=False)
            summary["rendered"] += 1
        except Exception as exc:  # one bad clip must not stop the batch; the error is stored below
            session.rollback()
            message = exc.message if isinstance(exc, AppError) else str(exc)
            _mark_failed(session, export.id, message)
            summary["failed"] += 1
            summary["errors"].append({"clip_id": clip.id, "message": message[:300]})
            get_logger().info(
                "render clip failed",
                extra={"project_id": project.id, "job_id": job.id, "stage": "render_clips"},
            )

    _progress(session, job, summary, 1.0)
    return summary


def _kept_clips(session: Session, project_id: str) -> list[Clip]:
    return (
        session.query(Clip)
        .filter(Clip.project_id == project_id, Clip.status != "rejected")
        .order_by(Clip.rank.asc(), Clip.t_start.asc())
        .all()
    )


def _has_ready_export(session: Session, clip_id: str) -> bool:
    return (
        session.query(ClipExport.id)
        .filter(ClipExport.clip_id == clip_id, ClipExport.status == "ready")
        .first()
        is not None
    )


def _mark_failed(session: Session, export_id: str, message: str) -> None:
    export = session.get(ClipExport, export_id)
    if export:
        export.status = "failed"
        export.error = message[:2000]
        session.add(export)
        session.commit()


def _progress(session: Session, job: Job, summary: dict[str, Any], progress: float = 0.0) -> None:
    checkpoint = dict(job.checkpoint or {})
    checkpoint["render_clips"] = {**summary, "errors": list(summary["errors"])}
    update_job(session, job, stage="render_clips", progress=render_progress(progress), checkpoint=checkpoint)
