"""segment_score stage: heuristics, optional LLM, cached candidates.json."""

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Job, Project
from app.pipeline.clip_plan import (
    clip_bounds,
    plan_reason,
    planned_clip_count,
    speech_seconds,
    window_clip_request,
)
from app.pipeline.job_state import update_job
from app.pipeline.llm_rank import llm_model_name, rank_with_llm
from app.pipeline.paths import rel_to_data, transcript_dir
from app.pipeline.score import PROMPT_VERSION, cache_key, score_candidates, transcript_sha

_AUTOPILOT_EXTRA_CANDIDATES = 5


def run_segment_score(session: Session, project: Project, job: Job) -> str:
    """Write transcript/candidates.json. LLM failures fall back to heuristics with the reason recorded."""
    settings = get_settings()
    update_job(session, job, stage="segment_score", progress=0.75)
    tdir = transcript_dir(project.id)
    words_path = tdir / "words.json"
    segments_path = tdir / "segments.json"
    if not words_path.exists():
        raise AppError("SCORE_FAILED", "Transcript words are missing", status_code=500)

    words = json.loads(words_path.read_text(encoding="utf-8"))
    segments = json.loads(segments_path.read_text(encoding="utf-8")) if segments_path.exists() else []
    if not isinstance(words, list):
        words = []
    if not isinstance(segments, list):
        segments = []

    media = float((project.source_meta or {}).get("duration") or 0)
    min_sec, max_sec = clip_bounds(project)
    requested = window_clip_request(
        project,
        (job.checkpoint or {}).get("live_window"),
        int(project.target_clip_count or settings.target_clip_count),
    )
    target = float(project.target_duration_sec or 45)
    plan = _autopilot_plan(words, requested, target, min_sec) if project.autopilot else None
    count = plan["planned"] if plan else requested
    keywords = [k.strip() for k in settings.keyword_boosts.split(",") if k.strip()]
    provider = (settings.llm_provider or "none").strip().lower()
    digest = transcript_sha(words)
    overlap = 0.0 if plan else 5.0
    heuristic_key = cache_key(
        digest,
        provider="none",
        model="heuristic",
        min_sec=min_sec,
        max_sec=max_sec,
        count=count,
        max_overlap=overlap,
    )
    key = heuristic_key
    if provider not in {"", "none"}:
        key = cache_key(
            digest,
            provider=provider,
            model=llm_model_name(provider),
            min_sec=min_sec,
            max_sec=max_sec,
            count=count,
            max_overlap=overlap,
        )

    out_path = tdir / "candidates.json"
    cached = _cached(out_path, key)
    if cached is not None:
        _record(session, job, out_path, cached, skipped=True)
        return str(cached.get("provider") or "heuristic")

    # Autopilot asks for spares: propose_windows drops overlaps after snapping and keeps the best `count`.
    ask = count + _AUTOPILOT_EXTRA_CANDIDATES if plan else count
    chosen: list[dict[str, Any]] = []
    used = "heuristic"
    llm_error: str | None = None
    if count > 0:
        chosen = score_candidates(
            words,
            segments,
            min_sec=min_sec,
            max_sec=max_sec,
            target_sec=target,
            count=ask,
            keywords=keywords,
            media_duration=media,
            max_overlap=overlap,
        )
        if provider not in {"", "none"}:
            ranked, llm_error = rank_with_llm(
                segments or words,
                min_sec=min_sec,
                max_sec=max_sec,
                target_count=ask,
                media_duration=media,
            )
            if ranked and plan and len(ranked) < count:
                ranked, llm_error = None, "LLM_TOO_FEW_CLIPS"
            if ranked:
                chosen, used = ranked, provider
    if used == "heuristic":
        key = heuristic_key

    payload = {
        "cache_key": key,
        "provider": used,
        "llm_error": llm_error,
        "prompt_version": PROMPT_VERSION,
        "plan": plan,
        "clips": chosen,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _record(session, job, out_path, payload, skipped=False)
    return used


def _autopilot_plan(words: list[dict[str, Any]], requested: int, target: float, min_sec: float) -> dict[str, Any]:
    speech = speech_seconds(words)
    planned = planned_clip_count(requested, speech, target, min_seconds=min_sec)
    return {
        "requested": requested,
        "planned": planned,
        "speech_seconds": speech,
        "reason": plan_reason(requested, planned, speech),
    }


def _cached(path: Path, key: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(existing, dict) and existing.get("cache_key") == key and existing.get("clips"):
        return existing
    return None


def _record(session: Session, job: Job, path: Path, payload: dict[str, Any], *, skipped: bool) -> None:
    checkpoint = dict(job.checkpoint or {})
    checkpoint["segment_score"] = {
        "skipped": skipped,
        "path": rel_to_data(path),
        "provider_used": payload.get("provider") or "heuristic",
        "llm_error": payload.get("llm_error"),
        "count": len(payload.get("clips") or []),
        "plan": payload.get("plan"),
    }
    update_job(session, job, progress=0.84, checkpoint=checkpoint)
