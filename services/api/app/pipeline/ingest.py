import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Asset, Job, Project
from app.pipeline.disk import ensure_free_disk
from app.pipeline.ffmpeg_util import ffprobe, has_audio, media_duration, sha256_file
from app.pipeline.job_state import update_job
from app.pipeline.paths import meta_dir, project_dir, rel_to_data
from app.pipeline.url_ingest import download_url

_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def find_raw(project_id: str) -> Path:
    raw = project_dir(project_id) / "raw"
    files = [p for p in raw.iterdir() if p.is_file() and p.suffix.lower() in _VIDEO_EXT] if raw.exists() else []
    if not files:
        raise AppError("INGEST_FAILED", "No raw source file found", status_code=500)
    files.sort(key=lambda p: (0 if p.suffix.lower() == ".mp4" else 1, p.name))
    return files[0]


def media_source(project_id: str, job: Job | None = None) -> Path:
    """Window file for a live job. find_raw stays on raw/ and never sees the follow."""
    live = (job.checkpoint or {}).get("live_window") if job is not None else None
    if isinstance(live, dict) and live.get("path"):
        path = (get_settings().data_path / str(live["path"])).resolve()
        if path.name in {"follow.mp4", "follow.mp4.part"} or path.name.endswith(".part"):
            raise AppError("INGEST_FAILED", "Refusing to ingest the live follow file", status_code=500)
        if not path.is_file():
            raise AppError("INGEST_FAILED", "Window file is missing", status_code=500)
        return path
    return find_raw(project_id)


def run_ingest(session: Session, project: Project, job: Job) -> dict:
    settings = get_settings()
    ensure_free_disk()
    update_job(session, job, stage="ingest", status="running", progress=0.05)
    live = isinstance((job.checkpoint or {}).get("live_window"), dict)
    if project.source_type == "url" and not live:
        download_url(project)
        session.add(project)
        session.commit()
    raw = media_source(project.id, job)
    checksum = sha256_file(raw)
    probe = ffprobe(raw)
    duration = media_duration(probe)

    if not has_audio(probe):
        raise AppError("INGEST_FAILED", "Source has no audio stream", status_code=400)
    if duration <= 0:
        raise AppError("INGEST_FAILED", "Could not determine media duration", status_code=400)
    if duration > settings.max_source_duration_sec:
        raise AppError(
            "INGEST_FAILED",
            f"Source longer than {settings.max_source_duration_sec}s",
            status_code=400,
        )

    probe_path = meta_dir(project.id) / "ffprobe.json"
    probe_path.write_text(json.dumps(probe, indent=2), encoding="utf-8")

    rel = rel_to_data(raw)
    existing = (
        session.query(Asset).filter(Asset.project_id == project.id, Asset.kind == "raw").one_or_none()
    )
    if existing:
        existing.path = rel
        existing.checksum = checksum
        existing.bytes = raw.stat().st_size
        existing.probe = probe
    else:
        session.add(
            Asset(
                project_id=project.id,
                kind="raw",
                path=rel,
                checksum=checksum,
                bytes=raw.stat().st_size,
                probe=probe,
            )
        )

    meta = dict(project.source_meta or {})
    if meta.get("chunked"):
        meta["chunk_duration"] = duration
    else:
        meta["duration"] = duration
    meta.update({"checksum": checksum, "filename": raw.name})
    project.source_meta = meta
    session.add(project)
    session.commit()

    checkpoint = dict(job.checkpoint or {})
    checkpoint["ingest"] = {"raw": rel, "checksum": checksum, "duration": duration}
    update_job(session, job, progress=0.15, checkpoint=checkpoint)
    return checkpoint["ingest"]
