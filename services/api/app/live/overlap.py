"""Drop a proposed clip when its broadcast interval mostly duplicates another."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Clip


def reject_overlapping_proposals(session: Session, project_id: str) -> int:
    """Keep the higher score. Mark the other proposed clip rejected with reason overlap.

    Accepted clips are a review decision and are left alone. A new proposal that
    overlaps one is the clip that gets rejected.
    """
    proposed = (
        session.query(Clip)
        .filter(
            Clip.project_id == project_id,
            Clip.status == "proposed",
            Clip.stream_offset_sec.is_not(None),
        )
        .all()
    )
    kept = (
        session.query(Clip)
        .filter(
            Clip.project_id == project_id,
            Clip.status.notin_(("proposed", "rejected")),
            Clip.stream_offset_sec.is_not(None),
        )
        .all()
    )
    losers: set[str] = set()
    for clip in proposed:
        if _loses_to_any(clip, kept):
            losers.add(clip.id)
    for i, left in enumerate(proposed):
        if left.id in losers:
            continue
        for right in proposed[i + 1 :]:
            if right.id in losers:
                continue
            if not _heavy(left, right):
                continue
            losers.add(_lower_score(left, right).id)
    changed = 0
    for clip in proposed:
        if clip.id not in losers:
            continue
        clip.status = "rejected"
        clip.reason = "overlap"
        session.add(clip)
        changed += 1
    if changed:
        session.commit()
    return changed


def _loses_to_any(clip: Clip, others: list[Clip]) -> bool:
    return any(_heavy(clip, other) for other in others)


def _lower_score(left: Clip, right: Clip) -> Clip:
    if left.score != right.score:
        return left if left.score < right.score else right
    left_at = float(left.stream_offset_sec or 0) + float(left.t_start)
    right_at = float(right.stream_offset_sec or 0) + float(right.t_start)
    return right if right_at >= left_at else left


def _heavy(left: Clip, right: Clip) -> bool:
    a0, a1 = _broadcast(left)
    b0, b1 = _broadcast(right)
    overlap = min(a1, b1) - max(a0, b0)
    if overlap <= 0:
        return False
    shorter = min(a1 - a0, b1 - b0)
    if shorter <= 0:
        return False
    return overlap > (shorter / 2) + 1e-6


def _broadcast(clip: Clip) -> tuple[float, float]:
    origin = float(clip.stream_offset_sec or 0)
    return origin + float(clip.t_start), origin + float(clip.t_end)
