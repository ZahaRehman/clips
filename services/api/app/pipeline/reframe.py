"""9:16 crop windows.

Coordinates on a clip are fractions of the source frame, origin top-left:

    {"mode": "face"|"center"|"left"|"right"|"custom"|"full", "x", "y", "w", "h"}

`full` is not a 9:16 window: it keeps the whole frame and exports landscape
(see `output_size`).

`x` and `y` are the top-left of the window; `w` and `h` are its size. All four
are in 0–1 source-frame units, then converted to even pixels at export.

Face mode samples the proxy (else the raw) about every 0.75s. Each frame keeps
the largest face by bounding-box area. The crop tracks the median of those
centers so a second, smaller face does not pull the frame. The face center sits
about 40% down the window for headroom. No detections fall back to a center
window and set `fallback` to `center`.
"""

from __future__ import annotations

from typing import Any

from app.errors import AppError

CROP_MODES = ("face", "center", "left", "right", "custom", "full")
FULL_MODE = "full"


def output_size(mode: str | None, vertical: tuple[int, int], full: tuple[int, int]) -> tuple[int, int]:
    """Export size for a crop mode: the whole screen is landscape, every other mode is the vertical short."""
    return full if mode == FULL_MODE else vertical
FACE_ANCHOR_Y = 0.40


def fitted_window(src_w: int, src_h: int, out_w: int, out_h: int) -> tuple[int, int]:
    target = out_w / out_h
    src_ar = src_w / max(src_h, 1)
    if abs(src_ar - target) < 0.01:
        return _even(src_w), _even(src_h)
    if src_ar > target:
        crop_h = src_h
        crop_w = int(round(src_h * target))
    else:
        crop_w = src_w
        crop_h = int(round(src_w / target))
    return max(2, _even(crop_w)), max(2, _even(crop_h))


def box_from_center(
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    cx_px: float,
    cy_px: float,
    mode: str,
    *,
    anchor_y: float = 0.5,
    fallback: str | None = None,
) -> dict[str, Any]:
    crop_w, crop_h = fitted_window(src_w, src_h, out_w, out_h)
    x = _clamp_origin(int(round(cx_px - crop_w / 2)), crop_w, src_w)
    y = _clamp_origin(int(round(cy_px - crop_h * anchor_y)), crop_h, src_h)
    box: dict[str, Any] = {
        "mode": mode,
        "x": x / src_w,
        "y": y / src_h,
        "w": crop_w / src_w,
        "h": crop_h / src_h,
    }
    if fallback:
        box["fallback"] = fallback
    return box


def static_crop(
    mode: str,
    stored: dict[str, Any],
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    t_start: float,
    t_end: float,
) -> dict[str, Any]:
    if mode == FULL_MODE:
        return {"mode": FULL_MODE, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0,
                "t_start": round(t_start, 3), "t_end": round(t_end, 3)}
    crop_w, crop_h = fitted_window(src_w, src_h, out_w, out_h)
    if mode == "left":
        cx, cy = crop_w / 2, src_h / 2
    elif mode == "right":
        cx, cy = src_w - crop_w / 2, src_h / 2
    elif mode == "custom":
        cx, cy = _custom_center_px(stored, src_w, src_h)
    else:
        cx, cy = src_w / 2, src_h / 2
        mode = "center" if mode not in CROP_MODES else mode
    box = box_from_center(src_w, src_h, out_w, out_h, cx, cy, mode if mode != "face" else "center")
    if mode == "center":
        box["mode"] = "center"
    box["t_start"] = round(t_start, 3)
    box["t_end"] = round(t_end, 3)
    return box


def face_crop(
    center: tuple[float, float] | None,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    t_start: float,
    t_end: float,
) -> dict[str, Any]:
    if center is None:
        box = box_from_center(
            src_w,
            src_h,
            out_w,
            out_h,
            src_w / 2,
            src_h / 2,
            "face",
            anchor_y=0.5,
            fallback="center",
        )
    else:
        box = box_from_center(
            src_w,
            src_h,
            out_w,
            out_h,
            center[0] * src_w,
            center[1] * src_h,
            "face",
            anchor_y=FACE_ANCHOR_Y,
        )
    box["t_start"] = round(t_start, 3)
    box["t_end"] = round(t_end, 3)
    return box


def box_matches_trim(crop: dict[str, Any], t_start: float, t_end: float) -> bool:
    if crop.get("x") is None or crop.get("w") is None or crop.get("h") is None or crop.get("y") is None:
        return False
    if "t_start" not in crop or "t_end" not in crop:
        return False
    return abs(float(crop["t_start"]) - t_start) < 0.02 and abs(float(crop["t_end"]) - t_end) < 0.02


def ffmpeg_crop_filter(box: dict[str, Any], src_w: int, src_h: int, out_w: int, out_h: int) -> str:
    if box.get("mode") == FULL_MODE:
        # Whole picture, never stretched: fit inside the frame and pad the rest black.
        return (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
    crop_w = max(2, _even(int(round(float(box["w"]) * src_w))))
    crop_h = max(2, _even(int(round(float(box["h"]) * src_h))))
    x = _clamp_origin(int(round(float(box["x"]) * src_w)), crop_w, src_w)
    y = _clamp_origin(int(round(float(box["y"]) * src_h)), crop_h, src_h)
    if crop_w >= src_w - 1 and crop_h >= src_h - 1:
        return f"scale={out_w}:{out_h}:flags=lanczos"
    # Lanczos keeps more detail than the default bicubic when a 9:16 window is upscaled.
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale={out_w}:{out_h}:flags=lanczos"


def median_face_center(per_frame: list[list[dict[str, float]]]) -> tuple[float, float] | None:
    centers: list[tuple[float, float]] = []
    for faces in per_frame:
        if not faces:
            continue
        biggest = max(faces, key=lambda face: face["area"])
        centers.append((biggest["cx"], biggest["cy"]))
    if not centers:
        return None
    return _median([c[0] for c in centers]), _median([c[1] for c in centers])


def _custom_center_px(stored: dict[str, Any], src_w: int, src_h: int) -> tuple[float, float]:
    try:
        x = float(stored["x"])
        y = float(stored["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("INVALID_CROP", "custom crop needs x and y between 0 and 1", status_code=400) from exc
    w = stored.get("w")
    h = stored.get("h")
    if w is None or h is None:
        return x * src_w, y * src_h
    return (x + float(w) / 2) * src_w, (y + float(h) / 2) * src_h


def _even(value: int) -> int:
    return value - (value % 2)


def _clamp_origin(origin: int, window: int, limit: int) -> int:
    origin = _even(max(0, origin))
    max_origin = _even(max(0, limit - window))
    return min(origin, max_origin)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
