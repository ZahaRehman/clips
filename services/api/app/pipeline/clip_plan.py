"""How many clips a source can support, and which length bounds apply."""

from __future__ import annotations

import math
from typing import Any

from app.config import get_settings
from app.models import Project

_SPEECH_GAP_SEC = 1.0


def clip_bounds(project: Project) -> tuple[float, float]:
    settings = get_settings()
    if project.autopilot:
        return float(settings.autopilot_clip_min_sec), float(settings.autopilot_clip_max_sec)
    return float(settings.clip_min_sec), float(settings.clip_max_sec)


def speech_seconds(words: list[dict[str, Any]]) -> float:
    """Word durations plus the short gaps between them. Long silences do not count."""
    spans: list[tuple[float, float]] = []
    for word in words:
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            spans.append((start, end))
    spans.sort()
    total = 0.0
    prev_end: float | None = None
    for start, end in spans:
        if prev_end is None:
            total += end - start
        elif start >= prev_end:
            gap = start - prev_end
            total += (gap if gap <= _SPEECH_GAP_SEC else 0.0) + (end - start)
        else:
            total += max(0.0, end - prev_end)
        prev_end = end if prev_end is None else max(prev_end, end)
    return round(total, 3)


def planned_clip_count(
    requested: int,
    speech_seconds: float,
    target_seconds: float,
    *,
    min_seconds: float = 0.0,
) -> int:
    """At most one clip per `target_seconds` of speech, never more than requested.

    A source shorter than the target but at least `min_seconds` long still gets one clip.
    """
    if requested <= 0 or speech_seconds <= 0 or target_seconds <= 0:
        return 0
    fit = math.floor(speech_seconds / target_seconds)
    if fit == 0 and min_seconds > 0 and speech_seconds >= min_seconds:
        fit = 1
    return max(0, min(requested, fit))


def plan_reason(requested: int, planned: int, speech_seconds: float) -> str:
    if planned >= requested:
        return ""
    minutes = speech_seconds / 60
    return f"Source has {minutes:.1f} min of speech, enough for {planned} of the {requested} requested clips."


def window_clip_request(project: Project, live_window: dict[str, Any] | None, requested: int) -> int:
    """Clip count for one window job.

    A chunked finished video spreads the project's count over its chunks by length, so
    a 10-clip request no longer renders up to 10 per chunk. A live window is capped by
    LIVE_WINDOW_MAX_CLIPS so rendering can keep up with a new window every few minutes.
    """
    if requested <= 0 or not isinstance(live_window, dict):
        return requested
    meta = project.source_meta or {}
    if meta.get("chunked"):
        total = float(meta.get("source_duration") or 0)
        try:
            length = float(live_window["stream_end_sec"]) - float(live_window["stream_start_sec"])
        except (KeyError, TypeError, ValueError):
            return requested
        if total <= 0 or length <= 0:
            return requested
        return max(1, min(requested, math.ceil(requested * length / total)))
    cap = get_settings().live_window_max_clips
    if project.source_type == "live" and cap > 0:
        return min(requested, cap)
    return requested
