"""Decide and copy closed windows. The follow file is never an ingest input."""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.errors import AppError
from app.pipeline.ffmpeg_util import ffprobe, has_audio, media_duration, run_cmd
from app.pipeline.paths import project_dir


def closed_span(last_cut: float, duration: float, chunk: float, lag: float) -> tuple[float, float] | None:
    """A window that ends at least `lag` seconds before the file's current duration."""
    if chunk <= 0 or duration <= 0:
        return None
    end = last_cut + chunk
    if duration + 1e-3 < end + lag:
        return None
    if end >= duration - 1e-3:
        return None
    return last_cut, end


def advance_cut(last_cut: float, chunk: float, overlap: float) -> float:
    step = chunk - overlap
    if step <= 0:
        step = chunk
    return last_cut + step


def tail_span(last_cut: float, duration: float, min_sec: float) -> tuple[float, float] | None:
    """Remaining finalized media after a clean exit. Not used while the file is still growing."""
    if duration - last_cut + 1e-3 < min_sec:
        return None
    if duration <= last_cut + 0.05:
        return None
    return last_cut, duration


def follow_paths(project_id: str) -> tuple[Path, Path]:
    folder = project_dir(project_id) / "capture"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "follow.mp4", folder / "follow.mp4.part"


def windows_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "capture" / "windows"
    path.mkdir(parents=True, exist_ok=True)
    return path


def window_file(project_id: str, index: int) -> Path:
    return windows_dir(project_id) / f"{index:04d}.mp4"


def growing_duration(path: Path) -> float:
    """ffprobe the file as it stands. A failure means 'not ready yet', not a seek into the tail."""
    if not path.is_file() or path.stat().st_size <= 0:
        return 0.0
    try:
        return media_duration(ffprobe(path))
    except (AppError, OSError, ValueError):
        return 0.0


def is_follow_file(path: Path) -> bool:
    name = path.name
    return name in {"follow.mp4", "follow.mp4.part"} or name.endswith(".part")


def copy_window(src: Path, start: float, duration: float, dest: Path) -> None:
    if is_follow_file(dest):
        raise AppError("INGEST_FAILED", "Refusing to treat the live follow as a window", status_code=500)
    settings = get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            settings.ffmpeg_path,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{duration:.3f}",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(dest),
        ]
    )
    probe = ffprobe(dest)
    if media_duration(probe) <= 0 or not has_audio(probe):
        dest.unlink(missing_ok=True)
        raise AppError("INGEST_FAILED", "Cut window has no playable audio", status_code=500)
