"""Capture loops. The supervisor process calls one of these and then finishes the project."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.config import get_settings
from app.db.sync import sync_session
from app.errors import AppError
from app.live.argv import live_follow_argv
from app.live.cut import (
    advance_cut,
    closed_span,
    copy_window,
    follow_paths,
    growing_duration,
    is_follow_file,
    tail_span,
    window_file,
)
from app.live.jobs import (
    next_index,
    queue_window_job,
    register_window,
    remember_cut,
    stream_origin_from_windows,
)
from app.models import Project
from app.pipeline.disk import ensure_free_disk
from app.pipeline.paths import project_dir


def run_youtube(project_id: str, stopped) -> tuple[str, str]:
    settings = get_settings()
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return "error", "Project missing"
        meta = dict(project.source_meta or {})
        url = (project.source_url or "").strip()
        origin = float(meta.get("stream_origin") or 0)
        last_cut = float(meta.get("follow_last_cut") or 0)
        index = next_index(session, project_id)
        resume = bool(meta.get("resume_pending"))
    if not url:
        return "error", "Live project has no URL"
    if resume:
        try:
            last_cut, index, origin = _archive_old_follow(project_id, last_cut, index, origin)
        except AppError as exc:
            if exc.code == "DISK_FULL":
                return "disk", exc.message
            raise
    final, partial = follow_paths(project_id)
    proc = _spawn_ytdlp(url, final)
    started = time.monotonic()
    last_size = -1
    last_growth = time.monotonic()
    try:
        while True:
            if stopped() or _stop_file(project_id):
                _terminate(proc)
                return "stopped", ""
            if time.monotonic() - started > settings.live_max_hours * 3600:
                _terminate(proc)
                return "limit", ""
            code = proc.poll()
            if code is not None:
                break
            media = partial if partial.is_file() else None
            if media is None:
                if time.monotonic() - last_growth >= settings.live_stall_sec:
                    _terminate(proc)
                    return "stall", ""
                time.sleep(2)
                continue
            size = media.stat().st_size
            if size != last_size:
                last_size = size
                last_growth = time.monotonic()
            elif time.monotonic() - last_growth >= settings.live_stall_sec:
                _terminate(proc)
                return "stall", ""
            try:
                last_cut, index, origin = _drain(project_id, media, last_cut, index, origin, allow_tail=False)
            except AppError as exc:
                if exc.code == "DISK_FULL":
                    _terminate(proc)
                    return "disk", exc.message
                raise
            time.sleep(2)
        clean = proc.returncode == 0
        media = final if final.is_file() else partial if partial.is_file() else None
        if clean and media is not None:
            try:
                _drain(project_id, media, last_cut, index, origin, allow_tail=True)
            except AppError as exc:
                if exc.code == "DISK_FULL":
                    return "disk", exc.message
                raise
        if not clean:
            return "error", _log_tail(project_id)
        return "ended", ""
    finally:
        if proc.poll() is None:
            _terminate(proc)


def _drain(
    project_id: str,
    media: Path,
    last_cut: float,
    index: int,
    origin: float,
    *,
    allow_tail: bool,
) -> tuple[float, int, float]:
    settings = get_settings()
    duration = growing_duration(media)
    while True:
        span = closed_span(last_cut, duration, settings.live_chunk_sec, settings.live_edge_lag_sec)
        if span is None:
            break
        start, end = span
        ensure_free_disk()
        dest = window_file(project_id, index)
        try:
            copy_window(media, start, end - start, dest)
        except AppError:
            return last_cut, index, origin
        _publish(project_id, index, origin + start, origin + end, dest, last_cut=advance_cut(last_cut, settings.live_chunk_sec, settings.live_chunk_overlap_sec), file_origin=origin, behind=duration - end)
        last_cut = advance_cut(last_cut, settings.live_chunk_sec, settings.live_chunk_overlap_sec)
        index += 1
        duration = growing_duration(media)
    if allow_tail:
        span = tail_span(last_cut, duration, settings.clip_min_sec)
        if span is not None:
            start, end = span
            ensure_free_disk()
            dest = window_file(project_id, index)
            try:
                copy_window(media, start, end - start, dest)
            except AppError as exc:
                if exc.code == "DISK_FULL":
                    raise
                return last_cut, index, origin
            _publish(
                project_id,
                index,
                origin + start,
                origin + end,
                dest,
                last_cut=end,
                file_origin=origin,
                behind=0,
            )
            last_cut = end
            index += 1
    with sync_session() as session:
        project = session.get(Project, project_id)
        if project:
            remember_cut(session, project, last_cut=last_cut, origin=origin, behind=max(0.0, duration - last_cut))
    return last_cut, index, origin


def _publish(
    project_id: str,
    index: int,
    stream_start: float,
    stream_end: float,
    dest: Path,
    *,
    last_cut: float,
    file_origin: float,
    behind: float = 0,
) -> None:
    if is_follow_file(dest):
        raise AppError("INGEST_FAILED", "Refusing to enqueue the live follow file", status_code=500)
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return
        window = register_window(
            session,
            project,
            index=index,
            stream_start=stream_start,
            stream_end=stream_end,
            path=dest,
        )
        remember_cut(session, project, last_cut=last_cut, origin=file_origin, behind=behind)
        queue_window_job(session, project, window)


def _archive_old_follow(project_id: str, last_cut: float, index: int, origin: float) -> tuple[float, int, float]:
    final, partial = follow_paths(project_id)
    media = partial if partial.is_file() else final if final.is_file() else None
    if media is not None:
        last_cut, index, origin = _drain(project_id, media, last_cut, index, origin, allow_tail=False)
        archive = project_dir(project_id) / "capture" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / f"{media.name}.{index}"
        media.rename(target)
    with sync_session() as session:
        project = session.get(Project, project_id)
        if project:
            origin = stream_origin_from_windows(session, project_id)
            meta = dict(project.source_meta or {})
            meta["resume_pending"] = False
            meta["follow_last_cut"] = 0
            meta["stream_origin"] = origin
            project.source_meta = meta
            session.add(project)
            session.commit()
    return 0.0, index, origin


def _spawn_ytdlp(url: str, output: Path) -> subprocess.Popen:
    argv = live_follow_argv(url, output)
    log_path = output.parent / "yt-dlp.log"
    handle = log_path.open("ab")
    try:
        return subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    finally:
        handle.close()


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def _stop_file(project_id: str) -> bool:
    return (project_dir(project_id) / "capture" / "STOP").is_file()


def _log_tail(project_id: str) -> str:
    path = project_dir(project_id) / "capture" / "yt-dlp.log"
    if not path.is_file():
        return "yt-dlp exited"
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return (text[-280:] or "yt-dlp exited")
