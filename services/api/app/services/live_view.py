"""Live fields on the project payload."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import LiveWindow, Project
from app.schemas.project import LiveStatus


async def live_payload(db: AsyncSession, project: Project) -> LiveStatus | None:
    if project.source_type != "live":
        return None
    meta = project.source_meta or {}
    result = await db.execute(select(LiveWindow.status).where(LiveWindow.project_id == project.id))
    statuses = list(result.scalars().all())
    settings = get_settings()
    behind = meta.get("behind_sec")
    if behind is None:
        behind = float(settings.live_chunk_sec) + float(settings.live_edge_lag_sec)
    return LiveStatus(
        mode=str(meta.get("capture_mode") or "youtube"),
        state=str(meta.get("live_state") or "starting"),
        behind_sec=float(behind),
        windows_ready=sum(1 for item in statuses if item == "ready"),
        windows_failed=sum(1 for item in statuses if item == "failed"),
        windows_saved=len(statuses),
    )
