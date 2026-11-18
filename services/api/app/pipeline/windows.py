"""Snap candidate windows to speech boundaries and store proposed clips."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.errors import AppError
from app.live.overlap import reject_overlapping_proposals
from app.models import Clip, Job, Project
from app.pipeline.clip_plan import clip_bounds
from app.pipeline.framing import crop_for_framing, framing_of
from app.pipeline.job_state import update_job
from app.pipeline.paths import transcript_dir

_PAUSE_SEC = 0.25
_PAD_SEC = 0.2
_WALK_SEC = 3.0


def snap_window(
    start: float,
    end: float,
    words: list[dict[str, Any]],
    media_duration: float = 0.0,
) -> tuple[float, float]:
    """Move edges onto pauses. The result never begins inside a word."""
    tokens = _clean_words(words)
    if not tokens or end <= start:
        capped = media_duration if media_duration and media_duration > 0 else end
        return max(0.0, start), max(start, min(end, capped) if capped else end)

    limit = media_duration if media_duration and media_duration > 0 else tokens[-1]["end"] + _PAD_SEC
    start_idx = _index_at(tokens, start)
    start_idx = _walk_to_pause(tokens, start_idx, direction=-1)
    end_idx = _index_at(tokens, end)
    end_idx = max(end_idx, start_idx)
    end_idx = _walk_to_pause(tokens, end_idx, direction=1)
    if end_idx < start_idx:
        end_idx = start_idx

    snapped_start = float(tokens[start_idx]["start"])
    snapped_end = float(tokens[end_idx]["end"])
    prev_end = float(tokens[start_idx - 1]["end"]) if start_idx > 0 else 0.0
    next_start = float(tokens[end_idx + 1]["start"]) if end_idx + 1 < len(tokens) else limit

    snapped_start = max(0.0, snapped_start - _PAD_SEC)
    if start_idx > 0:
        snapped_start = max(snapped_start, prev_end + 0.02)
    snapped_end = min(limit, snapped_end + _PAD_SEC)
    if end_idx + 1 < len(tokens):
        snapped_end = min(snapped_end, next_start - 0.02)

    if snapped_end <= snapped_start:
        snapped_start = float(tokens[start_idx]["start"])
        snapped_end = min(limit, float(tokens[end_idx]["end"]))
    return round(snapped_start, 3), round(max(snapped_end, snapped_start + 0.05), 3)


def cap_window(start: float, end: float, words: list[dict[str, Any]], max_sec: float) -> tuple[float, float]:
    """Pull the end back to the last word that fits, when snapping pushed a window past max_sec."""
    if end - start <= max_sec:
        return start, end
    tokens = [w for w in _clean_words(words) if w["start"] >= start - 0.05]
    limit = start + max_sec
    fitting = [i for i, w in enumerate(tokens) if w["end"] + _PAD_SEC <= limit]
    if not fitting:
        return start, round(limit, 3)
    last = fitting[-1]
    new_end = tokens[last]["end"] + _PAD_SEC
    if last + 1 < len(tokens):
        new_end = min(new_end, tokens[last + 1]["start"] - 0.02)
    return start, round(max(new_end, tokens[last]["end"]), 3)


def keep_non_overlapping(
    proposals: list[dict[str, Any]],
    keep: int,
    taken: list[tuple[float, float]],
) -> list[dict[str, Any]]:
    """Best-scoring windows that overlap neither each other nor `taken`, up to `keep`."""
    occupied = list(taken)
    kept: list[dict[str, Any]] = []
    for item in sorted(proposals, key=lambda p: p["score"], reverse=True):
        if len(kept) >= keep:
            break
        if any(min(item["t_end"], e) - max(item["t_start"], s) > 0.05 for s, e in occupied):
            continue
        kept.append(item)
        occupied.append((item["t_start"], item["t_end"]))
    return kept


def starts_inside_word(t: float, words: list[dict[str, Any]]) -> bool:
    for word in _clean_words(words):
        if word["start"] + 0.02 < t < word["end"] - 0.02:
            return True
    return False


def run_propose_windows(session: Session, project: Project, job: Job) -> int:
    update_job(session, job, stage="propose_windows", progress=0.88)
    path = transcript_dir(project.id) / "candidates.json"
    if not path.exists():
        raise AppError("PROPOSE_FAILED", "candidates.json is missing", status_code=500)
    raw = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    checkpoint = dict(job.checkpoint or {})
    if (checkpoint.get("propose") or {}).get("candidates_sha") == digest:
        update_job(session, job, progress=0.95, checkpoint=checkpoint)
        return int((checkpoint.get("propose") or {}).get("count") or 0)

    data = json.loads(raw)
    candidates = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(candidates, list):
        raise AppError("PROPOSE_FAILED", "candidates.json has no clips", status_code=500)

    words = _load_words(project.id)
    media = float((project.source_meta or {}).get("duration") or 0)
    proposals: list[dict[str, Any]] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        try:
            start = float(cand["start_sec"])
            end = float(cand["end_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        snapped_start, snapped_end = snap_window(start, end, words, media)
        if snapped_end <= snapped_start:
            continue
        proposals.append(
            {
                "t_start": snapped_start,
                "t_end": snapped_end,
                "score": float(cand.get("score") or 0),
                "title": str(cand.get("title") or "Clip")[:512],
                "reason": str(cand.get("reason") or "")[:2000],
            }
        )

    if project.autopilot:
        proposals = _autopilot_proposals(session, project, proposals, words, data, _stream_offset(job))
    proposals.sort(key=lambda item: item["score"], reverse=True)
    offset = _stream_offset(job)
    existing_kept = _kept_count(session, project.id) if project.autopilot else 0
    proposed = session.query(Clip).filter(Clip.project_id == project.id, Clip.status == "proposed")
    if offset is not None:
        proposed = proposed.filter(Clip.stream_offset_sec == offset)
    proposed.delete(synchronize_session=False)
    rank = _next_rank(session, project.id) if offset is not None else 1
    for item in proposals:
        session.add(
            Clip(
                project_id=project.id,
                rank=rank,
                t_start=item["t_start"],
                t_end=item["t_end"],
                score=item["score"],
                title=item["title"],
                reason=item["reason"],
                status="proposed",
                crop=crop_for_framing(framing_of(project.source_meta)),
                stream_offset_sec=offset,
            )
        )
        rank += 1
    session.commit()
    if offset is not None:
        reject_overlapping_proposals(session, project.id)

    checkpoint["propose"] = {"candidates_sha": digest, "count": len(proposals)}
    if project.autopilot:
        _note_kept_shortfall(checkpoint, data, existing_kept + len(proposals))
    update_job(session, job, progress=0.95, checkpoint=checkpoint)
    return len(proposals)


def _stream_offset(job: Job) -> float | None:
    live = (job.checkpoint or {}).get("live_window")
    if not isinstance(live, dict) or live.get("stream_start_sec") is None:
        return None
    return float(live["stream_start_sec"])


def _next_rank(session: Session, project_id: str) -> int:
    current = session.query(Clip.rank).filter(Clip.project_id == project_id).all()
    if not current:
        return 1
    return max(int(row[0] or 0) for row in current) + 1


def _kept_count(session: Session, project_id: str) -> int:
    return (
        session.query(Clip)
        .filter(Clip.project_id == project_id, Clip.status.notin_(("proposed", "rejected")))
        .count()
    )


def _note_kept_shortfall(checkpoint: dict[str, Any], data: Any, total_kept: int) -> None:
    """Say so when snapping leaves fewer clips than the speech-length plan."""
    plan = data.get("plan") if isinstance(data, dict) else None
    if not isinstance(plan, dict):
        return
    planned = int(plan.get("planned") or 0)
    if total_kept >= planned:
        return
    requested = int(plan.get("requested") or planned)
    speech = float(plan.get("speech_seconds") or 0)
    score = dict(checkpoint.get("segment_score") or {})
    score["plan"] = {
        **plan,
        "kept": total_kept,
        "reason": (
            f"Source has {speech / 60:.1f} min of speech, enough for {planned} of the {requested} requested clips. "
            f"Only {total_kept} fit without overlapping."
        ),
    }
    checkpoint["segment_score"] = score


def _autopilot_proposals(
    session: Session,
    project: Project,
    proposals: list[dict[str, Any]],
    words: list[dict[str, Any]],
    candidates: dict[str, Any],
    stream_offset: float | None = None,
) -> list[dict[str, Any]]:
    min_sec, max_sec = clip_bounds(project)
    fitted = []
    for item in proposals:
        start, end = cap_window(item["t_start"], item["t_end"], words, max_sec)
        if end - start + 0.05 < min_sec:
            continue
        fitted.append({**item, "t_start": start, "t_end": end})
    existing_query = session.query(Clip).filter(Clip.project_id == project.id, Clip.status != "proposed")
    if stream_offset is not None:
        existing_query = existing_query.filter(Clip.stream_offset_sec == stream_offset)
    existing = existing_query.all()
    taken = [(c.t_start, c.t_end) for c in existing]
    still_kept = sum(1 for c in existing if c.status != "rejected")
    plan = candidates.get("plan")
    planned = int(plan["planned"]) if isinstance(plan, dict) else len(fitted)
    return keep_non_overlapping(fitted, max(0, planned - still_kept), taken)


def _load_words(project_id: str) -> list[dict[str, Any]]:
    path = transcript_dir(project_id) / "words.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _clean_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for word in words:
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        cleaned.append({"word": str(word.get("word") or ""), "start": start, "end": end})
    cleaned.sort(key=lambda item: item["start"])
    return cleaned


def _index_at(words: list[dict[str, Any]], t: float) -> int:
    for i, word in enumerate(words):
        if t < word["end"]:
            return i
    return len(words) - 1


def _walk_to_pause(words: list[dict[str, Any]], index: int, direction: int) -> int:
    anchor = float(words[index]["start"] if direction < 0 else words[index]["end"])
    while True:
        nxt = index + direction
        if nxt < 0 or nxt >= len(words):
            break
        if direction < 0:
            gap = words[index]["start"] - words[nxt]["end"]
            traveled = anchor - words[nxt]["start"]
        else:
            gap = words[nxt]["start"] - words[index]["end"]
            traveled = words[nxt]["end"] - anchor
        if gap >= _PAUSE_SEC or traveled > _WALK_SEC:
            break
        index = nxt
    return index
