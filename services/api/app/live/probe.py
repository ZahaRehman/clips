"""yt-dlp -J probe. Metadata only; this never writes a media file."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.errors import AppError
from app.live.argv import probe_argv
from app.live.status import refusal_for
from app.logging_setup import get_logger
from app.redact import scrub_text

_UPLOAD = "Upload a file you have rights to instead."


def probe_live(url: str, timeout: float = 60) -> dict[str, Any]:
    """Return the info JSON when live_status is is_live. Otherwise raise."""
    info = fetch_info(url, timeout=timeout)
    refusal = refusal_for(str(info.get("live_status") or ""))
    if refusal:
        code, message = refusal
        raise AppError(code, message, status_code=400)
    return info


def live_status_for_url(url: str, timeout: float = 20) -> str | None:
    """Best-effort status for the finished-file URL form. None if the probe cannot run."""
    info = peek_url(url, timeout=timeout)
    if not info:
        return None
    status = info.get("live_status")
    return str(status) if status else None


def peek_url(url: str, timeout: float = 20) -> dict[str, Any] | None:
    """Metadata for the URL form. None if the probe cannot run."""
    try:
        return fetch_info(url, timeout=timeout)
    except Exception:
        get_logger().info("url create skipped the live probe")
        return None


def fetch_info(url: str, timeout: float = 60) -> dict[str, Any]:
    settings = get_settings()
    binary = settings.yt_dlp_path
    if shutil.which(binary) is None and not Path(binary).is_file():
        raise AppError("INGEST_FAILED", f"yt-dlp is not installed. {_UPLOAD}", status_code=500)
    try:
        result = subprocess.run(probe_argv(url), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            "INGEST_FAILED",
            f"Could not download this URL (probe timed out). {_UPLOAD}",
            status_code=400,
        ) from exc
    if result.returncode != 0:
        detail = _tail(result.stderr or result.stdout)
        raise AppError(
            "INGEST_FAILED",
            f"Could not download this URL ({detail}). {_UPLOAD}",
            status_code=400,
        )
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppError(
            "INGEST_FAILED",
            f"Could not download this URL (probe returned no metadata). {_UPLOAD}",
            status_code=400,
        ) from exc
    if not isinstance(info, dict):
        raise AppError(
            "INGEST_FAILED",
            f"Could not download this URL (probe returned no metadata). {_UPLOAD}",
            status_code=400,
        )
    return info


def _tail(text: str) -> str:
    line = scrub_text(" ".join((text or "").strip().split()))
    if not line:
        return "download unavailable"
    return line[-280:]
