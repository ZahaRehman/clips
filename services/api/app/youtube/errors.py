"""Classify YouTube upload and processing errors. Pure: no network."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

QUOTA_REASONS = {"quotaExceeded", "uploadLimitExceeded"}
NO_RETRY_REASONS = {"duplicate", "copyright", "claim"}
RETRYABLE_HTTP = {500, 502, 503, 504}
STOP_BATCH_CODES = QUOTA_REASONS | {"YOUTUBE_DISCONNECTED"}

QUOTA_MESSAGE = "YouTube rejected the upload. The quota resets at midnight Pacific."
DISCONNECTED_MESSAGE = "YouTube is disconnected. Connect the channel again. Already posted videos are unchanged."
BATCH_STOPPED_MESSAGE = "Stopped because an earlier upload hit YouTube's quota. It resets at midnight Pacific."


def classify_api_error(status: Optional[int], reasons: list[str]) -> str:
    """quota_stop halts the batch. no_retry fails the row. retry resumes the same session."""
    found = set(reasons)
    if found & QUOTA_REASONS:
        return "quota_stop"
    if found & NO_RETRY_REASONS:
        return "no_retry"
    if status in RETRYABLE_HTTP or status is None:
        return "retry"
    return "fail"


def stops_batch(error_code: Optional[str]) -> bool:
    return error_code in STOP_BATCH_CODES


def map_processing(
    *,
    upload_status: Optional[str],
    processing_status: Optional[str],
    failure_reason: Optional[str],
    rejection_reason: Optional[str],
    processing_failure_reason: Optional[str],
    publish_at: Optional[datetime],
    now: datetime,
) -> tuple[str, Optional[str]]:
    """Map a videos.list payload to a publish status and the reason to store."""
    error = rejection_reason or failure_reason or processing_failure_reason
    rejected = upload_status in {"rejected", "failed", "deleted"} or processing_status in {"failed", "terminated"}
    if rejected:
        return "failed", error or upload_status or processing_status
    done = processing_status == "succeeded" or upload_status == "processed"
    if done:
        if publish_at is not None and publish_at > now:
            return "scheduled", None
        return "live", None
    return "processing", None
