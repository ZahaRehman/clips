"""Gemini Developer API over native REST with JSON Schema output."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.config import get_settings
from app.errors import AppError
from app.logging_setup import get_logger

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_RETRY_WAIT_SEC = 5.0
_MAX_RETRY_WAIT_SEC = 30.0


class GeminiError(AppError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, wait: float = _RETRY_WAIT_SEC):
        super().__init__(code, message, status_code=502)
        self.retryable = retryable
        self.wait = wait


def generate_json(prompt: str, schema: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
    """One structured call. Retries once on transient errors; tries the fallback model on a spent daily quota."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_NO_KEY", "GEMINI_API_KEY is not set")
    primary = model or settings.gemini_model
    try:
        return _call_with_retry(prompt, schema, primary)
    except GeminiError as exc:
        fallback = settings.gemini_fallback_model
        if exc.code != "GEMINI_QUOTA_DAY" or not fallback or fallback == primary:
            raise
        get_logger().info("gemini fallback model", extra={"model": fallback})
        return _call_with_retry(prompt, schema, fallback)


def classify_quota(body: Any) -> tuple[str, float, str]:
    """Map a 429 body to (GEMINI_QUOTA_DAY | GEMINI_QUOTA_MINUTE, suggested wait, quota id)."""
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    quota_ids: list[str] = []
    wait = _RETRY_WAIT_SEC
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        for violation in item.get("violations") or []:
            if isinstance(violation, dict):
                quota_ids.append(str(violation.get("quotaId") or violation.get("quotaMetric") or ""))
        delay = item.get("retryDelay")
        if isinstance(delay, str):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", delay.strip())
            if match:
                wait = min(float(match.group(1)), _MAX_RETRY_WAIT_SEC)
    daily = [qid for qid in quota_ids if "perday" in qid.lower()]
    if daily:
        return "GEMINI_QUOTA_DAY", wait, daily[0]
    return "GEMINI_QUOTA_MINUTE", wait, next((qid for qid in quota_ids if qid), "")


def _call_with_retry(prompt: str, schema: dict[str, Any], model: str) -> dict[str, Any]:
    try:
        return _call(prompt, schema, model)
    except GeminiError as exc:
        if not exc.retryable:
            raise
        time.sleep(exc.wait)
        return _call(prompt, schema, model)


def _call(prompt: str, schema: dict[str, Any], model: str) -> dict[str, Any]:
    settings = get_settings()
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
    }
    timeout = httpx.Timeout(connect=10.0, read=settings.gemini_timeout_sec, write=30.0, pool=5.0)
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{BASE_URL}/models/{model}:generateContent",
            json=body,
            headers={"x-goog-api-key": settings.gemini_api_key},
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        _log(model, "timeout", started)
        raise GeminiError(
            "GEMINI_TIMEOUT",
            f"Gemini did not answer within {settings.gemini_timeout_sec:.0f}s",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        _log(model, "network", started)
        raise GeminiError("GEMINI_HTTP", f"Could not reach Gemini ({type(exc).__name__})") from exc

    payload = _json_or_none(response)
    if response.status_code == 429:
        code, wait, quota_id = classify_quota(payload)
        _log(model, "429", started, quota_id)
        if code == "GEMINI_QUOTA_DAY":
            raise GeminiError(code, "Gemini daily quota reached; resets at midnight Pacific")
        raise GeminiError(code, "Gemini per-minute quota reached", retryable=True, wait=wait)
    _log(model, str(response.status_code), started)
    if response.status_code >= 400:
        raise GeminiError(
            "GEMINI_HTTP",
            f"Gemini returned HTTP {response.status_code}: {_error_message(payload)}",
            retryable=response.status_code >= 500,
        )
    return _parse_candidate(payload)


def _parse_candidate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GeminiError("GEMINI_BAD_RESPONSE", "Gemini response was not JSON")
    blocked = (payload.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise GeminiError("GEMINI_BAD_RESPONSE", f"Gemini blocked the prompt ({blocked})")
    candidates = payload.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        raise GeminiError("GEMINI_BAD_RESPONSE", "Gemini returned no candidates")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
    if not text.strip():
        reason = candidates[0].get("finishReason") or "empty"
        raise GeminiError("GEMINI_BAD_RESPONSE", f"Gemini returned no text ({reason})")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError("GEMINI_BAD_RESPONSE", "Gemini output was not valid JSON") from exc
    if not isinstance(data, dict):
        raise GeminiError("GEMINI_BAD_RESPONSE", "Gemini output was not a JSON object")
    return data


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_message(payload: Any) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return str(message or "no details")[:300]


def _log(model: str, status: str, started: float, quota_id: str = "") -> None:
    extra: dict[str, Any] = {
        "model": model,
        "http_status": status,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    if quota_id:
        extra["quota_id"] = quota_id
    get_logger().info("gemini call", extra=extra)
