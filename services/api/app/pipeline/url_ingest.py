"""yt-dlp download for URL projects. No DRM, cookie, or bypass options."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from app.config import get_settings
from app.errors import AppError
from app.redact import scrub_text
from app.models import Project
from app.pipeline.paths import raw_dir

_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}

# H.264 decodes far faster on CPU than VP9/AV1, so it is the choice up to 1080p.
DOWNLOAD_FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"


def download_format(
    max_height: int | None = None, *, fallback: str = DOWNLOAD_FORMAT, sections: bool = False
) -> str:
    """yt-dlp `-f` for the source.

    A 9:16 window cut from 1080p is only 608 px wide and is stretched ~1.8x to 1080x1920.
    A 1440p source gives 810 px, so vertical shorts come out visibly sharper. YouTube
    serves 1440p only as VP9/AV1, so it is tried first (VP9 before AV1: faster to decode),
    and anything without a 1440p stream falls back to the 1080p H.264 choice above.

    `sections` (a time-range download) keeps to plain https streams: a VP9 HLS stream cut
    with --download-sections came back audio-only in testing.
    """
    height = int(max_height if max_height is not None else get_settings().source_max_height)
    if height <= 1080:
        return fallback
    # SDR only: an HDR (10-bit PQ) stream turned into 8-bit yuv420p looks washed out.
    band = f"[height<={height}][height>1080][dynamic_range=?SDR]"
    if sections:
        band += "[protocol^=http]"
    return (
        f"bv*{band}[vcodec^=avc1]+ba[ext=m4a]/"
        f"bv*{band}[vcodec~='^(vp0?9)']+ba[ext=m4a]/"
        f"bv*{band}+ba[ext=m4a]/"
        f"bv*{band}+ba/"
        f"{fallback}"
    )


def download_url(project: Project) -> None:
    if project.source_type != "url":
        return
    if _existing_video(project.id):
        return
    url = (project.source_url or "").strip()
    if not url:
        raise AppError("INGEST_FAILED", "URL project has no source URL", status_code=400)

    settings = get_settings()
    binary = settings.yt_dlp_path
    if shutil.which(binary) is None and not Path(binary).is_file():
        raise AppError(
            "INGEST_FAILED",
            "yt-dlp is not installed. Upload a file you have rights to instead.",
            status_code=500,
        )

    dest_dir = raw_dir(project.id)
    cmd = yt_dlp_argv(url, dest_dir / "source.%(ext)s")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
    except subprocess.TimeoutExpired as exc:
        _cleanup_partial(dest_dir)
        raise AppError(
            "INGEST_FAILED",
            "Download timed out. Upload a file you have rights to instead.",
            status_code=400,
        ) from exc

    video = _existing_video(project.id)
    if result.returncode != 0 or video is None:
        _cleanup_partial(dest_dir)
        detail = _tail(result.stderr or result.stdout)
        raise AppError(
            "INGEST_FAILED",
            f"Could not download this URL ({detail}). Upload a file you have rights to instead.",
            status_code=400,
        )

    if video.stat().st_size > settings.max_source_bytes:
        video.unlink(missing_ok=True)
        raise AppError(
            "INGEST_FAILED",
            "Downloaded file exceeds the size cap. Upload a shorter file you have rights to instead.",
            status_code=400,
        )

    _merge_info(project, dest_dir)


def yt_dlp_argv(url: str, output: Path) -> list[str]:
    """Public-URL download only. No cookies, login, or unplayable-format flags."""
    settings = get_settings()
    return [
        settings.yt_dlp_path,
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "-f",
        download_format(),
        "--merge-output-format",
        "mp4",
        "--max-filesize",
        str(settings.max_source_bytes),
        "--match-filter",
        f"duration <= {settings.max_source_duration_sec}",
        "--write-info-json",
        "-o",
        str(output),
        url,
    ]


def _merge_info(project: Project, dest_dir: Path) -> None:
    info_path = next(dest_dir.glob("*.info.json"), None)
    meta = dict(project.source_meta or {})
    if info_path and info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            info = {}
        if isinstance(info, dict):
            title = str(info.get("title") or "").strip()
            if title and not meta.get("title_from_user"):
                project.title = title[:512]
                meta["title"] = title[:512]
            if info.get("duration") is not None:
                meta["duration"] = info.get("duration")
            if info.get("uploader"):
                meta["uploader"] = str(info.get("uploader"))[:256]
            if info.get("webpage_url"):
                meta["webpage_url"] = str(info.get("webpage_url"))[:2000]
            elif info.get("original_url"):
                meta["webpage_url"] = str(info.get("original_url"))[:2000]
    if not meta.get("webpage_url"):
        meta["webpage_url"] = project.source_url
    project.source_meta = meta


def _existing_video(project_id: str) -> Path | None:
    folder = raw_dir(project_id)
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _VIDEO_EXT]
    if not files:
        return None
    files.sort(key=lambda p: (0 if p.suffix.lower() == ".mp4" else 1, p.name))
    return files[0]


def _cleanup_partial(dest_dir: Path) -> None:
    for path in dest_dir.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)


def _tail(text: str) -> str:
    line = scrub_text(" ".join((text or "").strip().split()))
    if not line:
        return "download unavailable"
    return line[-280:]
