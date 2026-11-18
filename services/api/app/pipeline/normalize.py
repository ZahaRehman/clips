"""normalize stage: 16 kHz WAV for ASR now, 720p proxy in the background.

The proxy is only needed for the review player, face sampling, and as an export
source after a chunk file is released. Encoding it used to block ASR for ~460 s
per 5-minute 1080p60 chunk. It now starts as a separate ffmpeg process while ASR,
scoring and the LLM run, and `finish_proxy` waits for it before render_clips.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Asset, Job, Project
from app.pipeline.ffmpeg_util import failure_message, run_cmd, x264_args
from app.pipeline.ingest import media_source
from app.pipeline.job_state import update_job
from app.pipeline.paths import audio_path, proxy_path, rel_to_data


@dataclass
class _PendingProxy:
    proc: subprocess.Popen
    partial: Path
    final: Path
    log: Path
    project_id: str
    checksum: str


# One worker thread runs one job at a time, keyed by job id so a crash cannot mix jobs.
_pending: dict[str, _PendingProxy] = {}


def run_normalize(session: Session, project: Project, job: Job, raw_checksum: str) -> None:
    settings = get_settings()
    update_job(session, job, stage="normalize", progress=0.2)
    raw = media_source(project.id, job)
    proxy = proxy_path(project.id)
    wav = audio_path(project.id)

    skip_proxy = proxy.exists() and _asset_checksum(session, project.id, "proxy") == raw_checksum
    skip_wav = wav.exists() and _asset_checksum(session, project.id, "audio_wav") == raw_checksum

    if not skip_wav:
        run_cmd([settings.ffmpeg_path, "-nostdin", "-y", "-i", str(raw), "-ac", "1", "-ar", "16000", "-vn", str(wav)])
        _upsert_asset(session, project.id, "audio_wav", wav, raw_checksum)

    if not skip_proxy:
        _start_proxy(job.id, project.id, raw, proxy, raw_checksum)

    checkpoint = dict(job.checkpoint or {})
    checkpoint["normalize"] = {
        "proxy": rel_to_data(proxy),
        "audio": rel_to_data(wav),
        "checksum": raw_checksum,
    }
    update_job(session, job, progress=0.4, checkpoint=checkpoint)


def proxy_argv(raw: Path, out: Path) -> list[str]:
    settings = get_settings()
    return [
        settings.ffmpeg_path,
        "-nostdin",
        "-y",
        "-i",
        str(raw),
        "-vf",
        "scale=-2:min(720\\,ih)",
        *x264_args(settings.proxy_preset, settings.proxy_max_fps),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(out),
    ]


def finish_proxy(session: Session, job_id: str) -> None:
    """Wait for this job's background proxy, then record it. No-op when none is running."""
    pending = _pending.pop(job_id, None)
    if pending is None:
        return
    code = pending.proc.wait()
    text = pending.log.read_text(encoding="utf-8", errors="replace") if pending.log.is_file() else ""
    pending.log.unlink(missing_ok=True)
    if code != 0 or not pending.partial.is_file():
        pending.partial.unlink(missing_ok=True)
        raise AppError("FFMPEG_FAILED", f"proxy: {failure_message(code, text)}", status_code=500)
    pending.partial.replace(pending.final)
    _upsert_asset(session, pending.project_id, "proxy", pending.final, pending.checksum)


def abort_proxy(job_id: str) -> None:
    """Stop a background proxy after the job failed, and drop its partial file."""
    pending = _pending.pop(job_id, None)
    if pending is None:
        return
    if pending.proc.poll() is None:
        pending.proc.terminate()
        try:
            pending.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pending.proc.kill()
            pending.proc.wait()
    pending.partial.unlink(missing_ok=True)
    pending.log.unlink(missing_ok=True)


def _start_proxy(job_id: str, project_id: str, raw: Path, proxy: Path, checksum: str) -> None:
    abort_proxy(job_id)
    partial = proxy.with_name("proxy.partial.mp4")
    log = proxy.with_name("proxy.ffmpeg.log")
    with log.open("wb") as handle:
        # stderr goes to a file: a PIPE nobody reads would fill and stall ffmpeg.
        proc = subprocess.Popen(
            proxy_argv(raw, partial), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=handle
        )
    _pending[job_id] = _PendingProxy(proc, partial, proxy, log, project_id, checksum)


def _asset_checksum(session: Session, project_id: str, kind: str) -> str | None:
    asset = session.query(Asset).filter(Asset.project_id == project_id, Asset.kind == kind).one_or_none()
    return asset.checksum if asset else None


def _upsert_asset(session: Session, project_id: str, kind: str, path: Path, checksum: str) -> None:
    rel = rel_to_data(path)
    existing = session.query(Asset).filter(Asset.project_id == project_id, Asset.kind == kind).one_or_none()
    if existing:
        existing.path = rel
        existing.checksum = checksum
        existing.bytes = path.stat().st_size
    else:
        session.add(
            Asset(project_id=project_id, kind=kind, path=rel, checksum=checksum, bytes=path.stat().st_size)
        )
    session.commit()
