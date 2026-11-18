"""Split a finished video into overlapping ranges. No network and no FFmpeg."""

from __future__ import annotations

from app.errors import AppError


def plan_ranges(
    duration: float,
    *,
    chunk: float,
    step: float,
    min_tail: float,
    max_duration: float,
) -> list[tuple[float, float]]:
    """Return (start, end) pairs. The last end is the duration. A tail shorter than min_tail is dropped."""
    if chunk <= 0 or step <= 0:
        raise AppError("INGEST_FAILED", "Chunk length must be greater than 0", status_code=500)
    if duration > max_duration:
        raise AppError(
            "INGEST_FAILED",
            f"Source longer than {max_duration:g}s",
            status_code=400,
        )
    if duration <= 0:
        return []
    ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + chunk, duration)
        if end - start < min_tail:
            break
        ranges.append((round(start, 3), round(end, 3)))
        if end >= duration:
            break
        start += step
    return ranges
