"""Sample a clip and return one face center in normalized source coordinates.

Frames come from the proxy when it exists, otherwise the export source. The
detector is MediaPipe's short-range BlazeFace model bundled under app/assets.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.errors import AppError
from app.pipeline.paths import proxy_path
from app.pipeline.reframe import median_face_center

SAMPLE_SEC = 0.75
MAX_SAMPLES = 24

_detector: Any = None


def face_model_path() -> Path:
    name = Path("assets") / "models" / "blaze_face_short_range.tflite"
    candidates = [
        Path(__file__).resolve().parents[1] / name,
        Path("/app/app") / name,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def analysis_video(project_id: str, source: Path) -> Path:
    proxy = proxy_path(project_id)
    if proxy.is_file():
        return proxy
    return source


def detect_face_center(video: Path, t_start: float, t_end: float) -> tuple[float, float] | None:
    frames = _sample_frames(video, t_start, t_end)
    if not frames:
        return None
    try:
        detector = _face_detector()
        per_frame: list[list[dict[str, float]]] = []
        try:
            import mediapipe as mp
        except ImportError as exc:
            raise _missing_mediapipe() from exc
        for frame in frames:
            image = mp.Image.create_from_file(str(frame))
            result = detector.detect(image)
            faces: list[dict[str, float]] = []
            width = max(image.width, 1)
            height = max(image.height, 1)
            for det in result.detections:
                bb = det.bounding_box
                area = float(bb.width * bb.height)
                if area <= 0:
                    continue
                faces.append(
                    {
                        "cx": (bb.origin_x + bb.width / 2) / width,
                        "cy": (bb.origin_y + bb.height / 2) / height,
                        "area": area,
                    }
                )
            per_frame.append(faces)
        return median_face_center(per_frame)
    finally:
        shutil.rmtree(frames[0].parent, ignore_errors=True)


def _sample_frames(video: Path, t_start: float, t_end: float) -> list[Path]:
    settings = get_settings()
    duration = max(0.05, t_end - t_start)
    tmp = Path(tempfile.mkdtemp(prefix="cliping-faces-"))
    pattern = tmp / "frame_%03d.jpg"
    cmd = [
        settings.ffmpeg_path,
        "-y",
        "-ss",
        f"{max(0.0, t_start):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(video),
        "-vf",
        f"fps=1/{SAMPLE_SEC}",
        "-frames:v",
        str(MAX_SAMPLES),
        "-q:v",
        "4",
        str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    frames = sorted(tmp.glob("frame_*.jpg"))
    if result.returncode != 0 or not frames:
        shutil.rmtree(tmp, ignore_errors=True)
        tail = (result.stderr or "could not sample frames").strip().splitlines()
        raise AppError("FACE_CROP_FAILED", (tail[-1] if tail else "could not sample frames")[:500], status_code=500)
    return frames


def _face_detector() -> Any:
    global _detector
    if _detector is not None:
        return _detector
    model = face_model_path()
    if not model.is_file():
        raise AppError("FACE_CROP_FAILED", f"Face model missing at {model}", status_code=500)
    try:
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core import base_options as base_opts
    except ImportError as exc:
        raise _missing_mediapipe() from exc
    options = vision.FaceDetectorOptions(
        base_options=base_opts.BaseOptions(model_asset_path=str(model)),
        min_detection_confidence=0.5,
    )
    _detector = vision.FaceDetector.create_from_options(options)
    return _detector


def _missing_mediapipe() -> AppError:
    return AppError(
        "FACE_CROP_FAILED",
        "Face crop needs mediapipe. Reinstall the API package, or switch this clip to center, left, or right.",
        status_code=500,
    )
