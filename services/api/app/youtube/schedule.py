"""Pure publish planning: schedule times, snapshots, and duplicate checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from app.errors import AppError
from app.pipeline.post_text import check_composed, check_title

SHORTS_MAX_SEC = 180
SHORTS_WARN_SEC = 60
SCHEDULE_HORIZON = timedelta(days=366)
PRIVACY = {"public", "unlisted", "private"}
ACTIVE_STATUSES = {"queued", "uploading", "processing", "scheduled", "live"}

Action = Literal["create", "reuse", "retry_same", "retry_fresh"]


@dataclass(frozen=True)
class ClipCandidate:
    clip_id: str
    export_id: Optional[str]
    export_hash: Optional[str]
    export_status: Optional[str]
    duration_sec: Optional[float]
    post_title: Optional[str]
    post_description: Optional[str]
    active_publish_id: Optional[str] = None
    failed_publish_id: Optional[str] = None
    failed_has_session: bool = False
    failed_video_sent: bool = False


@dataclass(frozen=True)
class PlannedRow:
    clip_id: str
    export_id: str
    export_hash: str
    title: str
    description: str
    privacy_status: str
    publish_at: Optional[datetime]
    action: Action
    existing_id: Optional[str]


@dataclass(frozen=True)
class PublishPlan:
    rows: list[PlannedRow]
    warnings: list[str]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def schedule_times(start_at: datetime, every_minutes: int, n: int, *, now: datetime) -> list[datetime]:
    """One time per clip. A time in the past is kept; the caller posts that clip immediately."""
    if n < 1:
        raise AppError("INVALID_SCHEDULE", "Schedule needs at least one clip", status_code=400)
    if every_minutes < 1:
        raise AppError("INVALID_SCHEDULE", "Spacing must be at least 1 minute", status_code=400)
    start = aware(start_at)
    current = aware(now)
    times = [start + timedelta(minutes=every_minutes * i) for i in range(n)]
    horizon = current + SCHEDULE_HORIZON
    if any(item > horizon for item in times):
        raise AppError("INVALID_SCHEDULE", "A publish time is more than a year away", status_code=400)
    return times


def privacy_for(requested: str, when: Optional[datetime], now: datetime) -> tuple[str, Optional[datetime]]:
    """Future slots are private until publish_at. A past or missing time posts immediately."""
    if requested not in PRIVACY:
        raise AppError("INVALID_PRIVACY", "Privacy must be public, unlisted, or private", status_code=400)
    if when is None or aware(when) <= aware(now):
        return requested, None
    return "private", aware(when)


def snapshot_text(title: str, description: str) -> tuple[str, str]:
    """The exact strings stored on the row and sent to YouTube. No extra suffix."""
    return check_title(title), check_composed(description)


def insert_body(
    *,
    title: str,
    description: str,
    category_id: str,
    privacy_status: str,
    publish_at: Optional[datetime],
    made_for_kids: bool,
    contains_synthetic_media: bool,
    language: Optional[str],
) -> dict:
    snippet: dict = {"title": title, "description": description, "categoryId": category_id}
    if language:
        snippet["defaultLanguage"] = language
    status: dict = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": made_for_kids,
        "containsSyntheticMedia": contains_synthetic_media,
    }
    if publish_at is not None:
        status["privacyStatus"] = "private"
        status["publishAt"] = aware(publish_at).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"snippet": snippet, "status": status}


def next_upload_action(video_id: Optional[str], session_uri: Optional[str]) -> str:
    """A stored video id is polled. A stored session is resumed. A new insert is the last resort."""
    if video_id:
        return "poll"
    if session_uri:
        return "resume"
    return "start"


def plan_batch(
    clips: list[ClipCandidate],
    *,
    privacy: str,
    start_at: Optional[datetime],
    every_minutes: Optional[int],
    made_for_kids: Optional[bool],
    rights_confirmed: bool,
    now: datetime,
) -> PublishPlan:
    if not rights_confirmed:
        raise AppError(
            "RIGHTS_REQUIRED",
            "Confirm you own these videos or have permission to post them",
            status_code=400,
        )
    if made_for_kids is None:
        raise AppError("MADE_FOR_KIDS_REQUIRED", "Choose whether these videos are made for kids", status_code=400)
    if privacy not in PRIVACY:
        raise AppError("INVALID_PRIVACY", "Privacy must be public, unlisted, or private", status_code=400)
    if not clips:
        raise AppError("NO_CLIPS", "Select at least one clip", status_code=400)

    times: list[Optional[datetime]]
    if start_at is None:
        times = [None] * len(clips)
    else:
        if every_minutes is None:
            raise AppError("INVALID_SCHEDULE", "Spacing is required with a start time", status_code=400)
        times = list(schedule_times(start_at, every_minutes, len(clips), now=now))

    rows: list[PlannedRow] = []
    warnings: list[str] = []
    for clip, when in zip(clips, times):
        rows.append(_plan_one(clip, privacy, when, now, warnings))
    if rows and all(row.action == "reuse" for row in rows):
        warnings.append("These clips are already posted or uploading. No new uploads were created.")
    return PublishPlan(rows=rows, warnings=warnings)


def _plan_one(
    clip: ClipCandidate,
    privacy: str,
    when: Optional[datetime],
    now: datetime,
    warnings: list[str],
) -> PlannedRow:
    if clip.export_status != "ready" or not clip.export_id or not clip.export_hash:
        raise AppError("EXPORT_NOT_READY", "Each selected clip needs a finished export", status_code=400)
    if clip.duration_sec is None:
        raise AppError("EXPORT_NOT_READY", "Export duration is missing. Export the clip again", status_code=400)
    if clip.duration_sec > SHORTS_MAX_SEC:
        raise AppError(
            "EXPORT_TOO_LONG",
            "A clip is longer than 180 seconds. Trim it and export again before posting",
            status_code=400,
        )
    if clip.duration_sec > SHORTS_WARN_SEC:
        warnings.append(
            f"{clip.clip_id} is {clip.duration_sec:.0f}s. Shorts over 60 seconds can be blocked by a copyright claim."
        )
    privacy_status, publish_at = privacy_for(privacy, when, now)
    title, description = snapshot_text(clip.post_title or "", clip.post_description or "")
    if clip.active_publish_id:
        action: Action = "reuse"
        existing = clip.active_publish_id
    elif clip.failed_publish_id and (clip.failed_video_sent or clip.failed_has_session):
        action = "retry_same"
        existing = clip.failed_publish_id
    elif clip.failed_publish_id:
        action = "retry_fresh"
        existing = clip.failed_publish_id
    else:
        action = "create"
        existing = None
    return PlannedRow(
        clip_id=clip.clip_id,
        export_id=clip.export_id,
        export_hash=clip.export_hash,
        title=title,
        description=description,
        privacy_status=privacy_status,
        publish_at=publish_at,
        action=action,
        existing_id=existing,
    )
