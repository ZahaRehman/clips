"""Fetch one finished-video range, then hand that file to the pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import get_settings
from app.db.sync import sync_session
from app.errors import AppError
from app.live.cut import copy_window
from app.live.jobs import enqueue_sync, queue_window_job, register_window
from app.models import Clip, ClipExport, LiveWindow, Project
from app.pipeline.disk import ensure_free_disk
from app.pipeline.ffmpeg_util import ffprobe, has_audio, media_duration
from app.pipeline.ingest import find_raw
from app.pipeline.paths import project_dir, rel_to_data
from app.redact import scrub_text
from app.vod.argv import section_argv
from app.vod.schedule import (
    FETCH_QUEUE,
    chunk_list,
    is_chunked,
    maybe_finish,
    next_planned,
    set_chunk_status,
)

def release_ready_chunk(session, project: Project, job) -> None:
    """Delete a chunk file once one of its shorts has a finished export."""
    if not is_chunked(project):
        return
    live = (job.checkpoint or {}).get("live_window") or {}
    rel = str(live.get("path") or "")
    if "/chunks/" not in rel.replace("\\", "/"):
        return
    offset = live.get("stream_start_sec")
    clips = (
        session.query(Clip)
        .filter(Clip.project_id == project.id, Clip.stream_offset_sec == offset)
        .all()
    )
    if not clips:
        return
    ready = (
        session.query(ClipExport.id)
        .filter(ClipExport.clip_id.in_([clip.id for clip in clips]), ClipExport.status == "ready")
        .first()
    )
    if not ready:
        return
    path = (get_settings().data_path / rel).resolve()
    root = (project_dir(project.id) / "chunks").resolve()
    if root not in path.parents or not path.is_file():
        return
    path.unlink()


def fetch_chunk(project_id: str, index: int) -> str:
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project or not is_chunked(project):
            raise AppError("INGEST_FAILED", "This project is not split into chunks", status_code=400)
        item = _item(project, index)
        if item is None:
            raise AppError("INGEST_FAILED", "Chunk range is missing", status_code=400)
        if str(item.get("status")) in {"saved", "ready"} and _chunk_path(project_id, index).is_file():
            return "skipped"
        try:
            ensure_free_disk()
        except AppError as exc:
            _fail(session, project, index, exc.message, stop=True)
            return "failed"
        set_chunk_status(project, index, "fetching")
        session.add(project)
        session.commit()
        dest = _chunk_path(project_id, index)
        try:
            _materialize(project, float(item["start"]), float(item["end"]), dest)
            _check_media(dest)
        except AppError as exc:
            dest.unlink(missing_ok=True)
            stop = exc.code == "DISK_FULL"
            _fail(session, project, index, exc.message, stop=stop)
            return "failed"
        except Exception as exc:
            dest.unlink(missing_ok=True)
            _fail(session, project, index, str(exc)[:500], stop=False)
            return "failed"
        _register(session, project, index, float(item["start"]), float(item["end"]), dest)
        set_chunk_status(project, index, "saved")
        session.add(project)
        session.commit()
        window = (
            session.query(LiveWindow)
            .filter(LiveWindow.project_id == project.id, LiveWindow.index == index)
            .one()
        )
        job = queue_window_job(session, project, window)
        if job.status == "failed":
            _fail(session, project, index, job.error or "Could not queue the pipeline job.", stop=False)
            return "failed"
        _queue_next(session, project, index)
        return "ok"


def _materialize(project: Project, start: float, end: float, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if project.source_type == "url":
        url = (project.source_url or "").strip()
        if not url:
            raise AppError("INGEST_FAILED", "URL project has no source URL", status_code=400)
        _download_section(url, dest, start, end)
        return
    copy_window(find_raw(project.id), start, max(0.1, end - start), dest)


def _download_section(url: str, dest: Path, start: float, end: float) -> None:
    settings = get_settings()
    cmd = section_argv(url, dest, start, end)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AppError("INGEST_FAILED", "Chunk download timed out.", status_code=400) from exc
    if result.returncode != 0 or not dest.is_file():
        detail = scrub_text(" ".join((result.stderr or result.stdout or "").split()))
        raise AppError(
            "INGEST_FAILED",
            f"Could not download this range ({detail[-280:] or 'download unavailable'}).",
            status_code=400,
        )
    if dest.stat().st_size > settings.max_source_bytes:
        raise AppError(
            "INGEST_FAILED",
            "Chunk exceeds the size cap. Upload a shorter file you have rights to instead.",
            status_code=400,
        )


def _check_media(path: Path) -> None:
    probe = ffprobe(path)
    if media_duration(probe) <= 0 or not has_audio(probe):
        raise AppError("INGEST_FAILED", "Chunk has no playable audio", status_code=400)


def _register(session, project: Project, index: int, start: float, end: float, dest: Path) -> None:
    existing = (
        session.query(LiveWindow)
        .filter(LiveWindow.project_id == project.id, LiveWindow.index == index)
        .one_or_none()
    )
    if existing:
        existing.path = rel_to_data(dest)
        existing.stream_start_sec = round(start, 3)
        existing.stream_end_sec = round(end, 3)
        existing.status = "queued"
        existing.error = None
        session.add(existing)
        session.commit()
        return
    register_window(session, project, index=index, stream_start=start, stream_end=end, path=dest)


def _fail(session, project: Project, index: int, message: str, *, stop: bool) -> None:
    set_chunk_status(project, index, "failed", message)
    if stop:
        meta = dict(project.source_meta or {})
        meta["chunk_error"] = message[:500]
        chunks = chunk_list(project)
        for item in chunks:
            other = int(item.get("index") or 0)
            if other != index and item.get("status") in {"planned", "fetching"}:
                item["status"] = "failed"
                item["error"] = message[:500]
        meta["chunks"] = chunks
        project.source_meta = meta
    session.add(project)
    session.commit()
    if not stop:
        _queue_next(session, project, index)
    maybe_finish(session, project)


def _queue_next(session, project: Project, index: int) -> None:
    session.refresh(project)
    nxt = next_planned(project, index)
    if nxt is None:
        maybe_finish(session, project)
        return
    try:
        enqueue_sync("run_fetch_chunk", project.id, str(nxt), queue=FETCH_QUEUE)
    except Exception as exc:
        set_chunk_status(project, nxt, "failed", "Could not queue the next chunk.")
        meta = dict(project.source_meta or {})
        meta["chunk_error"] = str(exc)[:500]
        project.source_meta = meta
        session.add(project)
        session.commit()
        maybe_finish(session, project)


def _item(project: Project, index: int) -> dict | None:
    for item in chunk_list(project):
        if int(item.get("index") or 0) == index:
            return item
    return None


def _chunk_path(project_id: str, index: int) -> Path:
    return project_dir(project_id) / "chunks" / f"{index:04d}.mp4"
