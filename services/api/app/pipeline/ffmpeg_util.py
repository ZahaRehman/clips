import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.errors import AppError


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def x264_args(preset: str, max_fps: float, *, crf: int | None = None) -> list[str]:
    """libx264 output options. `-fpsmax` caps 60 fps sources without raising slower ones."""
    args = ["-c:v", "libx264", "-preset", preset or "veryfast"]
    if crf is not None:
        args.extend(["-crf", str(crf)])
    if max_fps and max_fps > 0:
        args.extend(["-fpsmax", f"{max_fps:g}"])
    args.extend(["-pix_fmt", "yuv420p"])
    return args


def run_cmd(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise AppError("FFMPEG_FAILED", failure_message(result.returncode, result.stderr or result.stdout), status_code=500)
    return result


def failure_message(returncode: int, text: str) -> str:
    """A signal exit (OOM kill, container stop) leaves only warnings in stderr; say so instead."""
    if returncode < 0 or returncode in {137, 143}:
        signal = -returncode if returncode < 0 else returncode - 128
        return f"ffmpeg was killed (signal {signal}); the container may be out of memory or was stopped"
    return _last_error_line(text)


# libass warnings that never fail an encode but used to be reported as the cause.
_WARNINGS = ("error opening memory font",)


def _last_error_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    noise = ("frame=", "size=", "time=", "bitrate=", "speed=")
    useful = [line for line in lines if not line.startswith(noise) and not any(w in line.lower() for w in _WARNINGS)]
    markers = ("error", "failed", "invalid", "[h264", "conversion failed")
    errors = [line for line in useful if any(token in line.lower() for token in markers)]
    chosen = (errors or useful or lines or ["command failed"])[-1]
    return chosen[:500]


def ffprobe(path: Path) -> dict[str, Any]:
    settings = get_settings()
    cmd = [
        settings.ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = run_cmd(cmd)
    return json.loads(result.stdout)


def media_duration(probe: dict[str, Any]) -> float:
    fmt = probe.get("format") or {}
    if fmt.get("duration"):
        return float(fmt["duration"])
    for stream in probe.get("streams") or []:
        if stream.get("duration"):
            return float(stream["duration"])
    return 0.0


def has_audio(probe: dict[str, Any]) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe.get("streams") or [])


def video_size(probe: dict[str, Any]) -> tuple[int, int]:
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == "video":
            return int(stream.get("width") or 0), int(stream.get("height") or 0)
    return 0, 0
