"""Autopilot summary for the project response, read from job checkpoints and export rows."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clip, ClipExport, Job, Project
from app.schemas.project import AutopilotSummary


async def autopilot_summary(db: AsyncSession, project: Project) -> AutopilotSummary:
    pipeline = await _latest_checkpoint(db, project.id, ("pipeline",))
    newest = await _latest_checkpoint(db, project.id, ("pipeline", "metadata"))
    score = pipeline.get("segment_score") or {}
    plan = score.get("plan") or {}
    render = pipeline.get("render_clips") or {}
    metadata = newest.get("post_metadata") or pipeline.get("post_metadata") or {}
    return AutopilotSummary(
        requested=int(plan.get("requested") or project.target_clip_count),
        planned=plan.get("planned"),
        plan_reason=str(plan.get("reason") or ""),
        rendered=await _rendered_count(db, project.id),
        failed=int(render.get("failed") or 0),
        render_current=int(render.get("current") or 0),
        render_total=int(render.get("total") or 0),
        render_errors=list(render.get("errors") or []),
        provider_used=score.get("provider_used"),
        llm_error=score.get("llm_error"),
        metadata_source=metadata.get("source"),
        metadata_error=metadata.get("error"),
        metadata_error_message=metadata.get("error_message"),
    )


async def _latest_checkpoint(db: AsyncSession, project_id: str, kinds: tuple[str, ...]) -> dict[str, Any]:
    result = await db.execute(
        select(Job.checkpoint)
        .where(Job.project_id == project_id, Job.kind.in_(kinds))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    checkpoint = result.scalar_one_or_none()
    return checkpoint if isinstance(checkpoint, dict) else {}


async def _rendered_count(db: AsyncSession, project_id: str) -> int:
    result = await db.execute(
        select(func.count(func.distinct(ClipExport.clip_id)))
        .join(Clip, Clip.id == ClipExport.clip_id)
        .where(Clip.project_id == project_id, Clip.status != "rejected", ClipExport.status == "ready")
    )
    return int(result.scalar_one() or 0)
