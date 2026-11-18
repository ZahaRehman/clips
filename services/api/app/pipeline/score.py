"""Heuristic clip windows from word timestamps."""

from __future__ import annotations

import hashlib
import json
from typing import Any

PROMPT_VERSION = "p3"
DEFAULT_KEYWORDS = ("tip", "mistake", "secret", "why", "how", "lesson")
_INTERROGATIVES = {"why", "how", "what", "when", "who", "where", "which"}


def score_candidates(
    words: list[dict[str, Any]],
    segments: list[dict[str, Any]] | None = None,
    *,
    min_sec: float,
    max_sec: float,
    target_sec: float,
    count: int,
    keywords: list[str] | None = None,
    media_duration: float = 0.0,
    max_overlap: float = 5.0,
) -> list[dict[str, Any]]:
    """Return up to `count` windows with score, title, and reason.

    `max_overlap` is how many seconds two chosen windows may share. Manual ranking keeps a
    small overlap. Autopilot passes 0 so snapping cannot throw away a clip the plan counted on.
    """
    tokens = _usable_words(words, segments)
    if not tokens:
        return []

    keys = [k.strip().lower() for k in (keywords or DEFAULT_KEYWORDS) if k and k.strip()]
    target = min(max(target_sec, min_sec), max_sec)
    limit = media_duration if media_duration and media_duration > 0 else 0.0

    if limit and limit < min_sec:
        return _short_media_window(tokens, limit, keys)

    sentences = _sentences(tokens)
    raw: list[dict[str, Any]] = []
    for i in range(len(sentences)):
        window = _window_from(sentences, i, min_sec, max_sec, target, limit)
        if window is None:
            continue
        start, end, span_words = window
        score, reason = _score_span(span_words, end - start, target, min_sec, max_sec, keys)
        raw.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "score": score,
                "title": _title(span_words),
                "reason": reason,
            }
        )

    return _pick(raw, count, max_overlap=max_overlap)


def cache_key(
    transcript_sha: str,
    *,
    provider: str,
    model: str,
    min_sec: float,
    max_sec: float,
    count: int,
    max_overlap: float = 5.0,
) -> str:
    payload = "|".join(
        [
            PROMPT_VERSION,
            transcript_sha,
            provider,
            model,
            f"{min_sec:.3f}",
            f"{max_sec:.3f}",
            str(count),
            f"{max_overlap:.3f}",
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def transcript_sha(words: list[dict[str, Any]]) -> str:
    blob = json.dumps(words, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _usable_words(
    words: list[dict[str, Any]] | None,
    segments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    tokens = []
    for w in words or []:
        text = str(w.get("word") or "").strip()
        if not text:
            continue
        try:
            start = float(w["start"])
            end = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        tokens.append({"word": text, "start": start, "end": end})
    if tokens:
        tokens.sort(key=lambda w: w["start"])
        return tokens
    for seg in segments or []:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        tokens.append({"word": text, "start": start, "end": end})
    tokens.sort(key=lambda w: w["start"])
    return tokens


def _sentences(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    sentences: list[list[dict[str, Any]]] = []
    buf: list[dict[str, Any]] = []
    for i, word in enumerate(words):
        buf.append(word)
        gap = 0.0
        if i + 1 < len(words):
            gap = words[i + 1]["start"] - word["end"]
        if word["word"].endswith((".", "?", "!")) or gap > 0.4 or i == len(words) - 1:
            if buf:
                sentences.append(buf)
                buf = []
    if buf:
        sentences.append(buf)
    return sentences


def _window_from(
    sentences: list[list[dict[str, Any]]],
    index: int,
    min_sec: float,
    max_sec: float,
    target: float,
    media_duration: float,
) -> tuple[float, float, list[dict[str, Any]]] | None:
    start = float(sentences[index][0]["start"])
    if media_duration and start >= media_duration - 0.2:
        return None

    best: tuple[float, int, float] | None = None
    for j in range(index, len(sentences)):
        end = float(sentences[j][-1]["end"])
        if media_duration:
            end = min(end, media_duration)
        dur = end - start
        if dur > max_sec + 0.05:
            break
        if dur + 0.05 < min_sec:
            continue
        dist = abs(min(dur, max_sec) - target)
        if best is None or dist < best[0] - 1e-6:
            best = (dist, j, end)
        elif dur >= target:
            break
    if best is None:
        return None
    _, j, end = best
    span = [w for sent in sentences[index : j + 1] for w in sent if w["start"] < end]
    if not span or end - start + 0.05 < min_sec or end - start > max_sec + 0.05:
        return None
    return start, end, span


def _short_media_window(
    words: list[dict[str, Any]],
    media_duration: float,
    keywords: list[str],
) -> list[dict[str, Any]]:
    start = float(words[0]["start"])
    end = min(float(words[-1]["end"]), media_duration)
    if end - start < 1:
        return []
    span = [w for w in words if w["start"] < end]
    score, reason = _score_span(span, end - start, end - start, 1, max(end - start, 1), keywords)
    return [
        {
            "start_sec": round(max(0.0, start), 3),
            "end_sec": round(end, 3),
            "score": score,
            "title": _title(span),
            "reason": f"Source shorter than the clip minimum; {reason}",
        }
    ]


def _score_span(
    words: list[dict[str, Any]],
    duration: float,
    target: float,
    min_sec: float,
    max_sec: float,
    keywords: list[str],
) -> tuple[float, str]:
    text = " ".join(w["word"] for w in words)
    lowered = text.lower()
    score = 28.0
    reasons: list[str] = ["starts at a boundary"]

    questions = text.count("?")
    if questions:
        score += min(questions, 3) * 12
        reasons.append("question")
    first = words[0]["word"].lower().strip(".,!?\"'")
    if first in _INTERROGATIVES:
        score += 8
        reasons.append("interrogative start")
    bangs = text.count("!")
    if bangs:
        score += min(bangs, 3) * 8
        reasons.append("high-energy punctuation")
    hits = [k for k in keywords if k in lowered]
    if hits:
        score += min(len(hits), 4) * 6
        reasons.append("keywords " + ", ".join(hits[:3]))
    last = words[-1]["word"].rstrip()
    if last.endswith((".", "?", "!")):
        score += 10
        reasons.append("ends on a sentence")
    else:
        score -= 12
        reasons.append("mid-clause ending")

    span = max(1.0, max_sec - min_sec)
    fit = 1 - min(1.0, abs(duration - target) / span)
    score += 10 * fit
    score = max(0.0, min(100.0, score))
    return round(score, 1), "; ".join(reasons)


def _title(words: list[dict[str, Any]]) -> str:
    text = " ".join(w["word"].strip() for w in words)
    text = " ".join(text.split())
    for i, ch in enumerate(text):
        if ch in ".!?" and i >= 18:
            text = text[: i + 1]
            break
    if len(text) > 90:
        text = text[:87].rsplit(" ", 1)[0] + "…"
    return text or "Clip"


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return max(0.0, min(a["end_sec"], b["end_sec"]) - max(a["start_sec"], b["start_sec"]))


def _pick(candidates: list[dict[str, Any]], count: int, max_overlap: float) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda c: (-c["score"], c["start_sec"]))
    chosen: list[dict[str, Any]] = []
    for cand in ordered:
        if any(_overlap(cand, kept) > max_overlap for kept in chosen):
            continue
        chosen.append(cand)
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda c: c["start_sec"])
    return chosen
