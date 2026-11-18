"""yt-dlp argv for one finished-video range. No cookies, login, or bypass flags."""

from __future__ import annotations

from pathlib import Path

from app.errors import AppError
from app.live.argv import _BANNED
from app.pipeline.url_ingest import download_format, yt_dlp_argv

# Live capture bans section download. A finished video is the one place it is allowed.
_SECTION_BANNED = _BANNED - {"--download-sections"}

SECTION_FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b"


def section_argv(url: str, output: Path, start: float, end: float) -> list[str]:
    """Download one time range. The whole-video duration filter is not applied."""
    argv = yt_dlp_argv(url, output)
    argv = _drop_option(argv, "--match-filter")
    argv = _replace_option(argv, "-f", download_format(fallback=SECTION_FORMAT, sections=True))
    argv.insert(-1, "--download-sections")
    argv.insert(-1, f"*{start:.3f}-{end:.3f}")
    if "--force-keyframes-at-cuts" in argv:
        raise AppError("INGEST_FAILED", "Chunk download does not re-encode the cut.", status_code=500)
    _reject_section_bypass(argv)
    return argv


def _reject_section_bypass(argv: list[str]) -> None:
    for arg in argv:
        token = arg.split("=", 1)[0]
        if token in _SECTION_BANNED or token.startswith("--cookies"):
            raise AppError(
                "INGEST_FAILED",
                "Chunk download does not accept cookie, login, or bypass options.",
                status_code=400,
            )


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


def _replace_option(argv: list[str], flag: str, value: str) -> list[str]:
    out: list[str] = []
    skip_value = False
    replaced = False
    for arg in argv:
        if skip_value:
            skip_value = False
            if not replaced:
                out.append(value)
                replaced = True
            continue
        out.append(arg)
        if arg == flag:
            skip_value = True
    return out
