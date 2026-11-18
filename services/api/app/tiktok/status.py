"""Poll one Direct Post. A stored publish_id is never sent to init from here."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.tiktok.errors import BatchStop, RetryLater, RowFail, TikTokAPIError, classify_tiktok, message_for, raise_classified
from app.tiktok.oauth import api_json
from app.tiktok.rows import log_publish, update_publish

STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
_POLL_DELAYS = (0, 5, 10, 20, 30)


def poll_publish(local_id: str, publish_id: str | None) -> str:
    if not publish_id:
        raise RetryLater()
    for delay in _POLL_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            data = api_json("POST", STATUS_URL, {"publish_id": publish_id})
        except TikTokAPIError as exc:
            raise_classified(exc)
        outcome = apply_status(local_id, data)
        if outcome == "processing":
            continue
        log_publish(publish_id, outcome)
        return outcome
    update_publish(local_id, status="processing")
    return "poll_again"


def remote_offset(publish_id: str) -> int:
    try:
        data = api_json("POST", STATUS_URL, {"publish_id": publish_id})
    except TikTokAPIError as exc:
        raise_classified(exc)
    if str(data.get("status") or "") == "PUBLISH_COMPLETE":
        return 10**18
    raw = data.get("uploaded_bytes")
    if isinstance(raw, int) and raw > 0:
        return raw
    return 0


def apply_status(local_id: str, data: dict) -> str:
    status = str(data.get("status") or "")
    if status == "PUBLISH_COMPLETE":
        post_ids = data.get("publicaly_available_post_id") or []
        post_id = post_ids[0] if isinstance(post_ids, list) and post_ids else None
        update_publish(
            local_id,
            status="posted",
            tiktok_post_id=str(post_id) if post_id else None,
            finished_at=datetime.now(timezone.utc),
            fail_reason=None,
            error_message=None,
        )
        return "posted"
    if status == "FAILED":
        reason = str(data.get("fail_reason") or "FAILED")
        kind = classify_tiktok(reason)
        text = message_for(reason, reason)
        if kind == "stop_batch":
            raise BatchStop(reason, text)
        if kind == "retry":
            raise RetryLater()
        raise RowFail(reason, text)
    return "processing"
