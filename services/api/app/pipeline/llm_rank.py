"""Optional LLM ranking (Ollama, OpenAI-compatible, Gemini). Heuristics stay the fallback."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.llm.gemini import GeminiError, generate_json
from app.pipeline.score import _pick

_DEFAULT_TRANSCRIPT_CHARS = 14000

_CLIPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "score": {"type": "number"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start_sec", "end_sec", "score", "title", "reason"],
            },
        }
    },
    "required": ["clips"],
}


def llm_model_name(provider: str) -> str:
    settings = get_settings()
    if provider == "ollama":
        return settings.ollama_model
    if provider == "gemini":
        return settings.gemini_model
    return settings.openai_model


def rank_with_llm(
    segments: list[dict[str, Any]],
    *,
    min_sec: float,
    max_sec: float,
    target_count: int,
    media_duration: float,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return (clips, None) on success, (None, error_code) on failure, (None, None) when no provider is set."""
    settings = get_settings()
    provider = (settings.llm_provider or "none").strip().lower()
    if provider in {"", "none"}:
        return None, None
    if provider not in {"ollama", "openai_compatible", "gemini"}:
        return None, "LLM_UNKNOWN_PROVIDER"
    cap = settings.gemini_max_transcript_chars if provider == "gemini" else _DEFAULT_TRANSCRIPT_CHARS
    transcript = _transcript_block(segments, cap)
    if not transcript:
        return None, "LLM_EMPTY_TRANSCRIPT"
    instructions = _user_prompt(
        transcript,
        min_sec=min_sec,
        max_sec=max_sec,
        count=target_count,
        media_duration=media_duration,
    )
    try:
        if provider == "gemini":
            payload = generate_json(instructions, _CLIPS_SCHEMA)
        else:
            messages = [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": instructions},
            ]
            payload = parse_json_object(_complete(provider, messages))
    except GeminiError as exc:
        return None, exc.code
    except httpx.HTTPError:
        return None, "LLM_HTTP"
    except (ValueError, KeyError, IndexError):
        return None, "LLM_BAD_RESPONSE"
    clips = payload.get("clips")
    if not isinstance(clips, list):
        return None, "LLM_BAD_RESPONSE"
    valid = validate_clips(clips, min_sec=min_sec, max_sec=max_sec, media_duration=media_duration)
    if not valid:
        return None, "LLM_NO_VALID_CLIPS"
    return _pick(valid, target_count, max_overlap=5.0), None


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM JSON must be an object")
    return data


def validate_clips(
    clips: list[Any],
    *,
    min_sec: float,
    max_sec: float,
    media_duration: float,
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for item in clips:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start_sec", item.get("start")))
            end = float(item.get("end_sec", item.get("end")))
            score = float(item.get("score", 50))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        duration = end - start
        if duration + 0.05 < min_sec or duration > max_sec + 0.05:
            continue
        if start < -0.05:
            continue
        if media_duration and end > media_duration + 0.5:
            continue
        title = str(item.get("title") or "").strip()[:200] or "Clip"
        reason = str(item.get("reason") or "").strip()[:500] or "Ranked by model"
        valid.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "score": round(max(0.0, min(100.0, score)), 1),
                "title": title,
                "reason": reason,
            }
        )
    return valid


def _system_prompt() -> str:
    return (
        "You select short vertical clips from a transcript. "
        "Reply with JSON only, no markdown. "
        'Schema: {"clips":[{"start_sec":number,"end_sec":number,"score":number,'
        '"title":string,"reason":string}]}'
    )


def _user_prompt(
    transcript: str,
    *,
    min_sec: float,
    max_sec: float,
    count: int,
    media_duration: float,
) -> str:
    length = f"{media_duration:.1f} seconds" if media_duration else "unknown"
    return (
        "Pick the best moments of this video to post as standalone vertical Shorts.\n"
        f"Media duration: {length}.\n"
        f"Each clip must last between {min_sec:.0f} and {max_sec:.0f} seconds.\n"
        f"Return up to {count} clips, best first. Overlap between clips must be at most 5 seconds.\n"
        "Each clip must make sense without the rest of the video: a hook in the first three seconds, "
        "one complete thought, and an ending on a finished sentence, never mid-sentence.\n"
        "Consider the whole transcript, not just the beginning. Times must sit inside the transcript.\n"
        "score is 0-100 for how well the clip works on its own. title is a short label; "
        "reason says in one sentence why the moment works.\n\n"
        f"Transcript:\n{transcript}"
    )


def _transcript_block(segments: list[dict[str, Any]], cap: int) -> str:
    lines: list[str] = []
    total = 0
    for seg in segments:
        text = str(seg.get("text") or seg.get("word") or "").strip()
        if not text:
            continue
        try:
            start = float(seg.get("start", seg.get("start_sec")))
            end = float(seg.get("end", seg.get("end_sec")))
        except (TypeError, ValueError):
            continue
        line = f"[{start:.1f}-{end:.1f}] {text}"
        if total + len(line) > cap:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _complete(provider: str, messages: list[dict[str, str]]) -> str:
    settings = get_settings()
    timeout = httpx.Timeout(connect=5.0, read=settings.llm_timeout_sec, write=30.0, pool=5.0)
    if provider == "ollama":
        url = settings.ollama_base_url.rstrip("/") + "/api/chat"
        body = {"model": settings.ollama_model, "messages": messages, "stream": False, "format": "json"}
        response = httpx.post(url, json=body, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        content = message.get("content") or data.get("response") or ""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama returned an empty response")
        return content
    if provider == "openai_compatible":
        url = settings.openai_base_url.rstrip("/") + "/chat/completions"
        headers = {}
        if settings.openai_api_key:
            headers["Authorization"] = f"Bearer {settings.openai_api_key}"
        body = {
            "model": settings.openai_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        response = httpx.post(url, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Model returned an empty response")
        return content
    raise ValueError(f"Unknown LLM provider {provider}")
