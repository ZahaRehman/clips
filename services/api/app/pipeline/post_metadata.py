"""post_metadata stage: one Gemini call writes title, description, and hashtags for every kept clip."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.gemini import GeminiError, generate_json
from app.models import Clip, Job, Project, Transcript
from app.pipeline.job_state import update_job
from app.pipeline.paths import transcript_dir
from app.pipeline.post_text import (
    HASHTAGS_MAX,
    HASHTAGS_MIN,
    TITLE_MAX,
    clip_text,
    fit_description,
    metadata_hash,
    normalize_hashtags,
    sanitize_title,
)

_CLIP_TEXT_CHARS = 3000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "hashtags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": HASHTAGS_MIN,
                        "maxItems": HASHTAGS_MAX,
                    },
                },
                "required": ["clip_id", "title", "description", "hashtags"],
            },
        }
    },
    "required": ["clips"],
}


def run_post_metadata(session: Session, project: Project, job: Job) -> dict[str, Any]:
    """Draft post text for clips that need it. Never touches drafts the user edited."""
    model = get_settings().gemini_model
    update_job(session, job, stage="post_metadata")
    words = _load_words(project.id)
    clips = (
        session.query(Clip)
        .filter(Clip.project_id == project.id, Clip.status != "rejected")
        .order_by(Clip.rank.asc(), Clip.t_start.asc())
        .all()
    )
    live = (job.checkpoint or {}).get("live_window")
    if isinstance(live, dict) and live.get("stream_start_sec") is not None:
        offset = float(live["stream_start_sec"])
        clips = [clip for clip in clips if clip.stream_offset_sec == offset]
    pending: list[tuple[Clip, str, str]] = []
    for clip in clips:
        if clip.post_meta_source == "user":
            continue
        text = clip_text(words, clip.t_start, clip.t_end, clip.caption_text_override)
        digest = metadata_hash(text, model)
        if clip.post_meta_source == "gemini" and clip.post_meta_hash == digest:
            continue
        pending.append((clip, text, digest))

    result: dict[str, Any] = {"source": "skipped", "model": model, "error": None, "drafted": 0, "fallback": 0}
    if pending:
        drafts: dict[str, dict[str, Any]] = {}
        try:
            drafts = _ask_gemini(project, _language(session, project.id), pending)
        except GeminiError as exc:
            result["error"] = exc.code
            result["error_message"] = exc.message
        for index, (clip, _text, digest) in enumerate(pending):
            draft = drafts.get(_short_id(index))
            if draft:
                _apply_draft(clip, draft, digest)
                result["drafted"] += 1
            else:
                _apply_fallback(clip, digest)
                result["fallback"] += 1
        session.commit()
        if drafts and result["fallback"] and not result["error"]:
            result["error"] = "GEMINI_INCOMPLETE"
            result["error_message"] = f"Gemini skipped {result['fallback']} clip(s); they use fallback text"
        result["source"] = "gemini" if result["drafted"] else "fallback"

    checkpoint = dict(job.checkpoint or {})
    checkpoint["post_metadata"] = result
    update_job(session, job, checkpoint=checkpoint)
    return result


def _ask_gemini(project: Project, language: str, pending: list[tuple[Clip, str, str]]) -> dict[str, dict[str, Any]]:
    items = [{"clip_id": _short_id(i), "text": text[:_CLIP_TEXT_CHARS]} for i, (_c, text, _d) in enumerate(pending)]
    data = generate_json(_prompt(project, language, items), _SCHEMA)
    drafts: dict[str, dict[str, Any]] = {}
    for entry in data.get("clips") or []:
        if isinstance(entry, dict) and isinstance(entry.get("clip_id"), str):
            drafts[entry["clip_id"]] = entry
    return drafts


def _prompt(project: Project, language: str, items: list[dict[str, str]]) -> str:
    channel = get_settings().channel_context.strip() or "not given"
    return (
        "Write YouTube Shorts post text for each clip below. Each clip is posted on its own.\n"
        f"Source video title: {project.title or 'unknown'}\n"
        f"Channel: {channel}\n"
        f"Write in this language: {language or 'the language of the clip text'}\n\n"
        f"title: at most {TITLE_MAX} characters, makes a viewer want to watch, and only promises what the clip "
        "actually says. No hashtags or emoji spam in the title.\n"
        "description: one to three short sentences about this clip. No hashtags, no links.\n"
        f"hashtags: {HASHTAGS_MIN} to {HASHTAGS_MAX} relevant tags, each one word without spaces.\n"
        "Return exactly one entry per clip_id.\n\n"
        f"Clips:\n{json.dumps(items, ensure_ascii=False)}"
    )


def _apply_draft(clip: Clip, draft: dict[str, Any], digest: str) -> None:
    raw_tags = draft.get("hashtags")
    tags = normalize_hashtags(raw_tags if isinstance(raw_tags, list) else [])[:HASHTAGS_MAX]
    clip.post_title = sanitize_title(str(draft.get("title") or ""), clip.title)
    clip.post_hashtags = tags
    clip.post_description = fit_description(str(draft.get("description") or ""), tags)
    clip.post_meta_source = "gemini"
    clip.post_meta_hash = digest


def _apply_fallback(clip: Clip, digest: str) -> None:
    clip.post_title = sanitize_title(clip.title, "Clip")
    clip.post_description = ""
    clip.post_hashtags = []
    clip.post_meta_source = "fallback"
    clip.post_meta_hash = digest


def _short_id(index: int) -> str:
    return f"c{index + 1}"


def _language(session: Session, project_id: str) -> str:
    row = session.query(Transcript).filter(Transcript.project_id == project_id).one_or_none()
    return row.language if row else ""


def _load_words(project_id: str) -> list[dict[str, Any]]:
    path = transcript_dir(project_id) / "words.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []
