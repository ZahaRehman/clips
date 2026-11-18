"""Start and stop a live capture. The follow file is not a project raw."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.errors import AppError
from app.live.control import request_stop, start_supervisor
from app.live.probe import probe_live
from app.models import Project
from app.pipeline.framing import parse_framing
from app.schemas.project import ProjectCreateLive, ProjectCreated

router = APIRouter(tags=["live"])


@router.post("/projects/live", response_model=ProjectCreated, status_code=202)
async def start_live(body: ProjectCreateLive, db: AsyncSession = Depends(get_db)) -> ProjectCreated:
    settings = get_settings()
    if not settings.allow_live_ingest:
        raise AppError(
            "LIVE_INGEST_DISABLED",
            "Live ingest is disabled. Set ALLOW_LIVE_INGEST=true or upload a file you have rights to.",
            status_code=403,
        )
    if body.disclaimer_accepted is not True:
        raise AppError(
            "DISCLAIMER_REQUIRED",
            "Accept the rights disclaimer before submitting a URL",
            status_code=400,
        )
    url = (body.url or "").strip()
    folder_raw = (body.folder or "").strip()
    if bool(url) == bool(folder_raw):
        raise AppError("INVALID_REQUEST", "Send a live url or a folder, not both.", status_code=400)
    if url and not settings.allow_url_ingest:
        raise AppError(
            "URL_INGEST_DISABLED",
            "URL ingest is disabled. Set ALLOW_URL_INGEST=true or upload a file you have rights to.",
            status_code=403,
        )
    if url and not url.startswith(("http://", "https://")):
        raise AppError("INVALID_URL", "URL must start with http:// or https://", status_code=400)

    info = None
    folder = None
    if url:
        info = await asyncio.to_thread(probe_live, url)
    else:
        folder = resolve_folder(folder_raw)

    busy = await db.execute(select(Project.id).where(Project.status == "live").limit(1))
    if busy.scalar_one_or_none():
        raise AppError("LIVE_BUSY", "Another live capture is already running on this machine.", status_code=409)

    title = (body.title or "").strip()
    if not title and isinstance(info, dict):
        title = str(info.get("title") or "").strip()
    if not title:
        title = url or (folder.name if folder else "Live")
    mode = "youtube" if url else "folder"
    meta = {
        "capture_mode": mode,
        "disclaimer_accepted_at": datetime.now(timezone.utc).isoformat(),
        "live_state": "starting",
        "behind_sec": float(settings.live_chunk_sec) + float(settings.live_edge_lag_sec),
        "title_from_user": bool(body.title and body.title.strip()),
        "framing": parse_framing(body.framing),
    }
    if info is not None:
        meta["live_status"] = str(info.get("live_status") or "is_live")
        if info.get("webpage_url"):
            meta["webpage_url"] = str(info.get("webpage_url"))[:2000]
        else:
            meta["webpage_url"] = url
    if folder is not None:
        meta["folder"] = str(folder)
        meta["folder_seen"] = []
    from app.api.routes.projects import _new_project

    project = _new_project(
        title=title[:512],
        source_type="live",
        source_url=url or None,
        source_meta=meta,
        autopilot=body.autopilot,
        clip_count=body.clip_count,
    )
    project.status = "live"
    db.add(project)
    await db.commit()
    await db.refresh(project)
    try:
        await asyncio.to_thread(start_supervisor, project.id, resumed=False)
    except Exception as exc:
        project.status = "failed"
        meta = dict(project.source_meta or {})
        meta["live_state"] = "failed"
        meta["live_error"] = "Could not start the capture process."
        project.source_meta = meta
        await db.commit()
        raise AppError("LIVE_START_FAILED", "Could not start the capture process.", status_code=500) from exc
    return ProjectCreated(id=project.id, status=project.status)


@router.post("/projects/{project_id}/live/stop", response_model=ProjectCreated, status_code=202)
async def stop_live(project_id: str, db: AsyncSession = Depends(get_db)) -> ProjectCreated:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("NOT_FOUND", "Project not found", status_code=404)
    if project.source_type != "live":
        raise AppError("NOT_LIVE", "This project is not a live capture", status_code=400)
    if project.status == "live":
        await db.commit()
        await asyncio.to_thread(request_stop, project.id)
        await db.refresh(project)
    return ProjectCreated(id=project.id, status=project.status)


def resolve_folder(raw: str) -> Path:
    settings = get_settings()
    path = Path(raw).expanduser().resolve()
    roots = [settings.data_path.resolve()]
    if settings.live_folder_root.strip():
        roots.append(Path(settings.live_folder_root).expanduser().resolve())
    if not any(_inside(path, root) for root in roots):
        raise AppError(
            "INVALID_FOLDER",
            "Folder must be under DATA_DIR or LIVE_FOLDER_ROOT.",
            status_code=400,
        )
    if not path.is_dir():
        raise AppError("INVALID_FOLDER", "That folder does not exist.", status_code=400)
    return path


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
