"""yt-dlp argument lists for a public live probe and a follow. No cookies or bypass flags."""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.errors import AppError
from app.pipeline.url_ingest import yt_dlp_argv

# Exact tokens. `--no-live-from-start` is the off switch and is allowed.
_BANNED = {
    "--cookies",
    "--cookies-from-browser",
    "--username",
    "--password",
    "--netrc",
    "--video-password",
    "--download-sections",
    "--live-from-start",
    "--allow-unplayable-formats",
    "--extractor-args",
    "--geo-bypass",
    "--add-header",
}


def reject_bypass(argv: list[str]) -> None:
    for arg in argv:
        token = arg.split("=", 1)[0]
        if token in _BANNED or token.startswith("--cookies"):
            raise AppError(
                "INGEST_FAILED",
                "Live capture does not accept cookie, login, or bypass options.",
                status_code=400,
            )


def probe_argv(url: str) -> list[str]:
    settings = get_settings()
    argv = [
        settings.yt_dlp_path,
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--no-live-from-start",
        "-J",
        "--no-download",
        url,
    ]
    reject_bypass(argv)
    return argv


def live_follow_argv(url: str, output: Path) -> list[str]:
    """Public-URL argv without the duration filter or the mp4 merge, from the live edge."""
    argv = yt_dlp_argv(url, output)
    argv = _drop_option(argv, "--merge-output-format")
    argv = _drop_option(argv, "--match-filter")
    argv.insert(-1, "--no-live-from-start")
    reject_bypass(argv)
    return argv


def _drop_option(argv: list[str], flag: str) -> list[str]:
    out: list[str] = []
    skip_value = False
    for arg in argv:
        if skip_value:
            skip_value = False
            continue
        if arg == flag:
            skip_value = True
            continue
        out.append(arg)
    return out
