import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import Clip, ClipExport, Job, Project
from app.pipeline.export import planned_export_hash, resolve_export_source
from app.schemas.project import ExportOut
from app.services.enqueue import enqueue
from app.worker_queues import RENDER_QUEUE


def can_export(status: str, *, autopilot: bool) -> bool:
    """Manual projects need Accept. Autopilot may render proposals: rendering only writes a local file."""
    if status in {"accepted", "exported"}:
        return True
    return autopilot and status == "proposed"


def to_export_out(export: ClipExport, project_id: str) -> ExportOut:
    item = ExportOut.model_validate(export)
    if export.status == "ready" and export.path:
        prefix = f"projects/{project_id}/"
        rel = export.path[len(prefix) :] if export.path.startswith(prefix) else export.path
        item.download_url = f"/api/v1/files/{project_id}/{rel}"
    return item


async def queue_clip_export(db: AsyncSession, clip: Clip, *, force: bool) -> ExportOut:
    project = await db.get(Project, clip.project_id)
    if not project:
        raise AppError("NOT_FOUND", "Project not found", status_code=404)
    if not can_export(clip.status, autopilot=project.autopilot):
        raise AppError("NOT_ACCEPTED", "Accept the clip before export", status_code=400)
    await asyncio.to_thread(resolve_export_source, project.id, clip)

    if not force:
        reused = await _reuse_ready(db, clip)
        if reused:
            return reused

    export = ClipExport(clip_id=clip.id, preset="shorts_1080", status="pending")
    job = Job(
        project_id=project.id,
        kind="export",
        status="queued",
        stage="queued",
        progress=0.0,
        checkpoint={"force": True} if force else None,
    )
    db.add(export)
    db.add(job)
    if project.status != "live":
        project.status = "exporting"
    await db.commit()
    await db.refresh(export)
    await db.refresh(job)

    try:
        await enqueue("run_export", clip.id, export.id, job.id, queue=RENDER_QUEUE)
    except Exception as exc:
        job.status = "failed"
        job.error = "Could not queue the export."
        job.finished_at = datetime.now(timezone.utc)
        export.status = "failed"
        if project.status != "live":
            project.status = "ready_for_review"
        await db.commit()
        raise AppError(
            "EXPORT_FAILED",
            "Could not queue the export. Check that Redis and the worker are running.",
            status_code=503,
        ) from exc

    return to_export_out(export, project.id)


async def _reuse_ready(db: AsyncSession, clip: Clip) -> ExportOut | None:
    digest = await asyncio.to_thread(planned_export_hash, clip)
    if not digest:
        return None
    hash_path = (
        get_settings().data_path / "projects" / clip.project_id / "clips" / clip.id / "export.hash"
    )
    if not hash_path.is_file() or hash_path.read_text(encoding="utf-8").strip() != digest:
        return None
    result = await db.execute(
        select(ClipExport)
        .where(ClipExport.clip_id == clip.id, ClipExport.status == "ready")
        .order_by(ClipExport.created_at.desc())
    )
    export = result.scalars().first()
    if not export or not export.path:
        return None
    if not (get_settings().data_path / export.path).is_file():
        return None
    return to_export_out(export, clip.project_id)
