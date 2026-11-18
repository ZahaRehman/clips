"""Folder of finished segments. A file is a window once its size stops changing."""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.db.sync import sync_session
from app.errors import AppError
from app.models import Project

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".ts", ".mpegts"}


@dataclass
class SeenFile:
    size: int
    since: float


def list_videos(folder: Path) -> list[tuple[Path, int]]:
    if not folder.is_dir():
        return []
    found: list[tuple[Path, int]] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in VIDEO_EXT:
            continue
        if path.name.endswith(".part"):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        found.append((path, size))
    return found


def take_stable(
    files: list[tuple[Path, int]],
    seen: dict[str, SeenFile],
    now: float,
    stable_sec: float,
    already: set[str],
) -> list[Path]:
    """Return paths whose size has been unchanged for stable_sec. Growing files stay out."""
    names = {str(path) for path, _size in files}
    ready: list[Path] = []
    for path, size in files:
        key = str(path)
        prev = seen.get(key)
        if prev is None or prev.size != size:
            seen[key] = SeenFile(size, now)
            continue
        if now - prev.since < stable_sec:
            continue
        if key in already:
            continue
        ready.append(path)
    for key in list(seen):
        if key not in names:
            del seen[key]
    return ready


def run_folder(project_id: str, stopped) -> tuple[str, str]:
    from app.live.capture import _publish, _stop_file
    from app.live.cut import growing_duration, window_file
    from app.live.jobs import next_index, stream_origin_from_windows
    from app.pipeline.disk import ensure_free_disk

    settings = get_settings()
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return "error", "Project missing"
        meta = dict(project.source_meta or {})
        folder = Path(str(meta.get("folder") or ""))
        already = {str(item) for item in (meta.get("folder_seen") or [])}
        origin = stream_origin_from_windows(session, project_id)
        index = next_index(session, project_id)
    if not folder.is_dir():
        return "error", "Folder is not available"
    seen: dict = {}
    started = time.monotonic()
    while True:
        if stopped() or _stop_file(project_id):
            return "stopped", ""
        if time.monotonic() - started > settings.live_max_hours * 3600:
            return "limit", ""
        ready = take_stable(
            list_videos(folder),
            seen,
            time.monotonic(),
            settings.live_folder_stable_sec,
            already,
        )
        for path in ready:
            duration = growing_duration(path)
            if duration <= 0:
                continue
            try:
                ensure_free_disk()
            except AppError as exc:
                return "disk", exc.message
            dest = window_file(project_id, index)
            _adopt(path, dest)
            _publish(project_id, index, origin, origin + duration, dest, last_cut=0, file_origin=origin)
            already.add(str(path))
            _remember_folder(project_id, already)
            origin += duration
            index += 1
        time.sleep(2)


def _adopt(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def _remember_folder(project_id: str, seen: set[str]) -> None:
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return
        meta = dict(project.source_meta or {})
        meta["folder_seen"] = sorted(seen)
        meta["live_state"] = "running"
        project.source_meta = meta
        session.add(project)
        session.commit()
