"""Public live-video sample from the YouTube Data API. Metadata only. No download."""

from __future__ import annotations

import threading
from html import unescape
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.errors import AppError
from app.logging_setup import get_logger

YOUTUBE = "https://www.googleapis.com/youtube/v3"
SAMPLE_NOTE = "A ranked sample of public live videos. Not every stream on YouTube."
OFF_MESSAGE = "Live search is off until YOUTUBE_API_KEY is set. Paste a live URL instead."
QUOTA_MESSAGE = "Live search is exhausted until midnight Pacific."
FAIL_MESSAGE = "YouTube live search failed. Paste a live URL instead."
PAGE_SIZE = 25
PUBLIC_TTL_SEC = 45 * 60
QUERY_TTL_SEC = 10 * 60
OWN_TTL_SEC = 60
OWN_STATUSES = {"live", "liveStarting"}
_THUMB_HOSTS = {"i.ytimg.com", "yt3.ggpht.com", "yt3.googleusercontent.com"}
_lock = threading.Lock()
_public: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_own: Optional[tuple[float, list[dict[str, Any]]]] = None


def clear_live_cache() -> None:
    global _own
    with _lock:
        _public.clear()
        _own = None


def clean_filters(q: str, region: str, category: str) -> tuple[str, str, str]:
    query = " ".join((q or "").split())
    if len(query) > 80:
        raise AppError("LIVE_SEARCH_FILTER", "Search text is limited to 80 characters.", status_code=400)
    code = (region or "").strip().upper()
    if code and (len(code) != 2 or not code.isalpha()):
        raise AppError("LIVE_SEARCH_FILTER", "Region must be a two-letter country code.", status_code=400)
    cat = (category or "").strip()
    if cat and (not cat.isdigit() or len(cat) > 4):
        raise AppError("LIVE_SEARCH_FILTER", "Category must be a YouTube category id.", status_code=400)
    return query, code, cat


def public_ttl(query: str) -> int:
    return QUERY_TTL_SEC if query else PUBLIC_TTL_SEC


def rows_from_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        video_id = str(((item.get("id") or {}).get("videoId") or "")).strip()
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        if not _video_id(video_id) or snippet.get("liveBroadcastContent") != "live":
            continue
        rows.append(_card(video_id, snippet, own=False))
    return rows


def apply_video_details(rows: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    details: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("id") or "").strip()
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        live = item.get("liveStreamingDetails") if isinstance(item.get("liveStreamingDetails"), dict) else {}
        if video_id:
            details[video_id] = (snippet, live)
    kept: list[dict[str, Any]] = []
    for row in rows:
        pair = details.get(row["video_id"])
        if pair is None:
            continue
        snippet, live = pair
        if snippet.get("liveBroadcastContent") != "live" or live.get("actualEndTime"):
            continue
        row["concurrent_viewers"] = _viewers(live.get("concurrentViewers"))
        started = live.get("actualStartTime")
        row["started_at"] = started if isinstance(started, str) and started else None
        kept.append(row)
    return kept


def rows_from_broadcasts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        if status.get("lifeCycleStatus") not in OWN_STATUSES:
            continue
        video_id = str(item.get("id") or "").strip()
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        if not _video_id(video_id):
            continue
        card = _card(video_id, snippet, own=True)
        started = snippet.get("actualStartTime")
        card["started_at"] = started if isinstance(started, str) and started else None
        rows.append(card)
    return rows


def merge_streams(own: list[dict[str, Any]], public: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in own + public:
        video_id = row["video_id"]
        if video_id in seen:
            continue
        seen.add(video_id)
        merged.append(row)
    return merged


def quota_reason(status: int, body: Any) -> Optional[str]:
    if status not in (403, 429) or not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None
    reasons = [
        str(item.get("reason"))
        for item in (err.get("errors") or [])
        if isinstance(item, dict) and item.get("reason")
    ]
    for reason in reasons:
        if reason in {"quotaExceeded", "dailyLimitExceeded"}:
            return reason
    return None


def load_discovery(
    key: str,
    q: str,
    region: str,
    category: str,
    token: Optional[str],
    *,
    client: Any,
    now: float,
) -> dict[str, Any]:
    query, code, cat = clean_filters(q, region, category)
    with _lock:
        public_rows, response_region, cached = _public_rows(key, query, code, cat, client, now)
        own_rows = _own_rows(token, client, now)
    return {
        "enabled": True,
        "cached": cached,
        "region": response_region or None,
        "message": SAMPLE_NOTE,
        "streams": merge_streams(own_rows, public_rows),
    }


def _public_rows(
    key: str,
    query: str,
    region: str,
    category: str,
    client: Any,
    now: float,
) -> tuple[list[dict[str, Any]], str, bool]:
    cache_key = (query, region, category)
    hit = _public.get(cache_key)
    if hit and hit[0] > now:
        payload = hit[1]
        return [dict(row) for row in payload["streams"]], str(payload.get("region") or ""), True
    params: dict[str, Any] = {
        "part": "snippet",
        "type": "video",
        "eventType": "live",
        "order": "viewCount",
        "maxResults": PAGE_SIZE,
        "key": key,
    }
    if query:
        params["q"] = query
    if region:
        params["regionCode"] = region
    if category:
        params["videoCategoryId"] = category
    body = _youtube_get(client, "search", params)
    return _store_public(cache_key, body, key, client, now)


def _store_public(
    cache_key: tuple[str, str, str],
    search_body: dict[str, Any],
    key: str,
    client: Any,
    now: float,
) -> tuple[list[dict[str, Any]], str, bool]:
    found = rows_from_search(search_body)
    rows = apply_video_details(found, _videos(client, key, found))
    response_region = str(search_body.get("regionCode") or "")
    query = cache_key[0]
    _public[cache_key] = (now + public_ttl(query), {"streams": rows, "region": response_region})
    return rows, response_region, False


def _videos(client: Any, key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"items": []}
    return _youtube_get(
        client,
        "videos",
        {
            "part": "snippet,liveStreamingDetails",
            "id": ",".join(row["video_id"] for row in rows),
            "key": key,
            "maxResults": PAGE_SIZE,
        },
    )


def _own_rows(token: Optional[str], client: Any, now: float) -> list[dict[str, Any]]:
    global _own
    if not token:
        return []
    if _own and _own[0] > now:
        return [dict(row) for row in _own[1]]
    try:
        body = _youtube_get(
            client,
            "liveBroadcasts",
            {"part": "snippet,status", "mine": "true", "maxResults": 50},
            headers={"Authorization": f"Bearer {token}"},
        )
    except AppError as exc:
        get_logger().info(
            "youtube own broadcast list failed",
            extra={"stage": "youtube", "error_code": exc.code},
        )
        _own = (now + OWN_TTL_SEC, [])
        return []
    rows = rows_from_broadcasts(body)
    _own = (now + OWN_TTL_SEC, rows)
    return rows


def _youtube_get(client: Any, path: str, params: dict[str, Any], headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    try:
        response = client.get(f"{YOUTUBE}/{path}", params=params, headers=headers, timeout=15)
    except httpx.HTTPError as exc:
        get_logger().info("youtube live search failed", extra={"stage": "youtube", "error_code": "YOUTUBE_LIVE_SEARCH"})
        raise AppError("YOUTUBE_LIVE_SEARCH", FAIL_MESSAGE, status_code=502) from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    status = int(getattr(response, "status_code", 0) or 0)
    if status >= 400:
        reason = quota_reason(status, body)
        if reason:
            get_logger().info("youtube live search quota", extra={"stage": "youtube", "error_code": reason})
            raise AppError("YOUTUBE_SEARCH_QUOTA", QUOTA_MESSAGE, status_code=429)
        get_logger().info(
            "youtube live search failed",
            extra={"stage": "youtube", "error_code": "YOUTUBE_LIVE_SEARCH", "http_status": status},
        )
        raise AppError("YOUTUBE_LIVE_SEARCH", FAIL_MESSAGE, status_code=502)
    return body if isinstance(body, dict) else {}


def _card(video_id: str, snippet: dict[str, Any], *, own: bool) -> dict[str, Any]:
    thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    medium = thumbs.get("medium") if isinstance(thumbs.get("medium"), dict) else {}
    default = thumbs.get("default") if isinstance(thumbs.get("default"), dict) else {}
    thumb = str(medium.get("url") or default.get("url") or "")
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": unescape(str(snippet.get("title") or "")).strip(),
        "channel_title": unescape(str(snippet.get("channelTitle") or "")).strip(),
        "channel_id": str(snippet.get("channelId") or "").strip(),
        "thumbnail_url": thumb if _thumb_ok(thumb) else "",
        "concurrent_viewers": None,
        "started_at": None,
        "own": own,
    }


def _video_id(value: str) -> bool:
    if not value or len(value) < 6 or len(value) > 32:
        return False
    return all(ch.isalnum() or ch in "_-" for ch in value)


def _viewers(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _thumb_ok(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in _THUMB_HOSTS
