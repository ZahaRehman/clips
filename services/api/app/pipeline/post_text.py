"""Rules for post title, description, and hashtags. Pure functions shared by the stage and the API."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.errors import AppError
from app.pipeline.captions import resolve_caption_words

PROMPT_VERSION = "m1"
TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
HASHTAGS_MIN = 3
HASHTAGS_MAX = 5

_ANGLE = re.compile(r"[<>]")
_NOT_TAG = re.compile(r"[^\w]", re.UNICODE)


def check_title(raw: str) -> str:
    """User-entered title: reject instead of silently changing it."""
    title = " ".join(raw.split())
    if not title:
        raise AppError("INVALID_POST_TITLE", "Title cannot be empty", status_code=400)
    if _ANGLE.search(title):
        raise AppError("INVALID_POST_TITLE", "Title cannot contain < or >", status_code=400)
    if len(title) > TITLE_MAX:
        raise AppError("INVALID_POST_TITLE", f"Title must be at most {TITLE_MAX} characters", status_code=400)
    return title


def sanitize_title(raw: str, fallback: str) -> str:
    """Model-written title: clean it up, cut on a word boundary, or fall back."""
    title = " ".join(_ANGLE.sub("", raw or "").split())
    if len(title) > TITLE_MAX:
        cut = title[: TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
        title = (cut or title[: TITLE_MAX - 1]) + "…"
    if title:
        return title
    fallback_title = " ".join(_ANGLE.sub("", fallback or "").split())[:TITLE_MAX]
    return fallback_title or "Clip"


def clean_description(raw: str) -> str:
    lines = [line.rstrip() for line in _ANGLE.sub("", raw or "").strip().splitlines()]
    return "\n".join(lines).strip()


def normalize_hashtags(items: list[Any]) -> list[str]:
    """`#word` tags (letters, digits, underscore), deduped case-insensitively, order kept."""
    seen: set[str] = set()
    tags: list[str] = []
    for item in items:
        word = _NOT_TAG.sub("", str(item or "").lstrip("#"))
        if not word or word.casefold() in seen:
            continue
        seen.add(word.casefold())
        tags.append(f"#{word}")
    return tags


def check_hashtags(items: list[Any]) -> list[str]:
    tags = normalize_hashtags(items)
    if len(tags) > HASHTAGS_MAX:
        raise AppError("INVALID_HASHTAGS", f"Use at most {HASHTAGS_MAX} hashtags", status_code=400)
    return tags


def compose_description(description: str, hashtags: list[str]) -> str:
    body = clean_description(description)
    tail = " ".join(hashtags)
    return f"{body}\n\n{tail}" if body and tail else (body or tail)


def check_composed(composed: str) -> str:
    if len(composed.encode("utf-8")) >= DESCRIPTION_MAX_BYTES:
        raise AppError(
            "INVALID_POST_DESCRIPTION",
            f"Description plus hashtags must be under {DESCRIPTION_MAX_BYTES} bytes",
            status_code=400,
        )
    return composed


def fit_description(description: str, hashtags: list[str]) -> str:
    """Compose, trimming the body (never the hashtags) until it fits under the byte limit."""
    body = clean_description(description)
    composed = compose_description(body, hashtags)
    while body and len(composed.encode("utf-8")) >= DESCRIPTION_MAX_BYTES:
        body = body[: max(0, len(body) - 100)].rstrip()
        composed = compose_description(body, hashtags)
    return composed


def description_body(composed: str | None, hashtags: list[str] | None) -> str:
    """The editable part of a composed description: everything before the hashtag line."""
    text = composed or ""
    tail = " ".join(hashtags or [])
    if tail and text.endswith(tail):
        text = text[: -len(tail)]
    return text.rstrip()


def clip_text(words: list[dict[str, Any]], t_start: float, t_end: float, override: str | None) -> str:
    """What the viewer hears in the clip, including any caption text override."""
    selected = resolve_caption_words(words, t_start, t_end, override)
    return " ".join(str(w.get("word") or "").strip() for w in selected).strip()


def metadata_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{model}|{text}".encode()).hexdigest()
