"""How a project frames 9:16 shorts.

`face` follows the person. `gameplay` keeps a center window on the picture,
so a webcam in the corner is not the subject. `full` keeps the whole stream
screen as a 16:9 landscape video, with no crop.
"""

from __future__ import annotations

from typing import Any

from app.errors import AppError

FRAMINGS = ("face", "gameplay", "full")


def parse_framing(value: str | None) -> str:
    if value is None or value == "":
        return "face"
    if value not in FRAMINGS:
        raise AppError("INVALID_FRAMING", "framing must be face, gameplay or full", status_code=400)
    return value


def framing_of(source_meta: dict[str, Any] | None) -> str:
    raw = (source_meta or {}).get("framing")
    if raw in FRAMINGS:
        return str(raw)
    return "face"


def crop_for_framing(framing: str) -> dict[str, str]:
    if framing == "gameplay":
        return {"mode": "center"}
    if framing == "full":
        return {"mode": "full"}
    return {"mode": "face"}
