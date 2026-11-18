"""Resumable videos.insert. The session URI is saved before any media bytes are sent."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.db.sync import sync_session
from app.logging_setup import get_logger
from app.models import ClipExport, YouTubePublish
from app.redact import scrub_text
from app.youtube.errors import (
    BATCH_STOPPED_MESSAGE,
    DISCONNECTED_MESSAGE,
    QUOTA_MESSAGE,
    classify_api_error,
    map_processing,
    stops_batch,
)
from app.youtube.oauth import (
    YouTubeDisconnected,
    cached_category_check,
    fresh_credentials,
    remember_category_check,
    youtube_client,
)
from app.youtube.schedule import insert_body, next_upload_action

_POLL_DELAYS = (0, 5, 10, 20, 30)
_UPLOAD_DELAYS = (0, 2, 4, 8, 16, 32)
_PROCESSING_LIMIT = timedelta(minutes=45)
_CHUNK = 8 * 1024 * 1024


class RetryableUpload(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class QuotaStop(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FatalUpload(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Prep:
    publish_id: str
    project_id: str
    path: str
    title: str
    description: str
    privacy_status: str
    publish_at: Optional[datetime]
    notify: bool
    made_for_kids: bool
    synthetic: bool
    video_id: Optional[str]
    session_uri: Optional[str]
    language: Optional[str]
    created_at: datetime
    action: str


def execute_publish(publish_id: str) -> str:
    prep = _prepare(publish_id)
    if isinstance(prep, str):
        return prep
    try:
        creds = fresh_credentials()
        client = youtube_client(creds)
        category_id = _ensure_category(client)
        if prep.action == "poll":
            return _poll(client, prep)
        video_id = _upload(client, prep, category_id)
        _update(publish_id, youtube_video_id=video_id, status="processing")
        prep.video_id = video_id
        _log(publish_id, video_id, "uploaded")
        return _poll(client, prep)
    except YouTubeDisconnected:
        _fail(publish_id, "YOUTUBE_DISCONNECTED", DISCONNECTED_MESSAGE)
        _stop_queued(prep.project_id, publish_id, "YOUTUBE_DISCONNECTED", DISCONNECTED_MESSAGE)
        _log(publish_id, prep.video_id, "YOUTUBE_DISCONNECTED")
        return "stopped"
    except QuotaStop as exc:
        _fail(publish_id, exc.code, exc.message)
        _stop_queued(prep.project_id, publish_id, exc.code, BATCH_STOPPED_MESSAGE)
        _log(publish_id, prep.video_id, exc.code)
        return "stopped"
    except FatalUpload as exc:
        _fail(publish_id, exc.code, scrub_text(exc.message))
        _log(publish_id, prep.video_id, exc.code)
        return "failed"
    except RetryableUpload as exc:
        _log(publish_id, prep.video_id, "retry")
        _update(publish_id, error_message=scrub_text(exc.message))
        return "retry"


def unfinished_publish_ids() -> list[str]:
    with sync_session() as session:
        rows = (
            session.query(YouTubePublish)
            .filter(YouTubePublish.status.in_(("queued", "uploading", "processing")))
            .all()
        )
        return [row.id for row in rows]


def _prepare(publish_id: str) -> Prep | str:
    with sync_session() as session:
        row = session.get(YouTubePublish, publish_id)
        if row is None:
            return "missing"
        if row.status in {"scheduled", "live"}:
            return row.status
        if row.status == "failed" and stops_batch(row.error_code):
            return "stopped"
        export = session.get(ClipExport, row.export_id)
        path = _export_file(export)
        if path is None:
            _mark(row, "failed", "EXPORT_MISSING", "The export file is missing. Export the clip again.")
            return "failed"
        action = next_upload_action(row.youtube_video_id, row.session_uri)
        if action == "poll":
            row.status = "processing"
        else:
            row.status = "uploading"
        # YouTube rejects some detected codes, such as Norwegian Nynorsk (`nn`).
        language = "en"
        return Prep(
            publish_id=row.id,
            project_id=row.project_id,
            path=str(path),
            title=row.title,
            description=row.description,
            privacy_status=row.privacy_status,
            publish_at=_as_utc(row.publish_at),
            notify=row.notify_subscribers,
            made_for_kids=row.made_for_kids,
            synthetic=row.contains_synthetic_media,
            video_id=row.youtube_video_id,
            session_uri=row.session_uri,
            language=language,
            created_at=_as_utc(row.created_at) or datetime.now(timezone.utc),
            action=action,
        )


def _upload(client, prep: Prep, category_id: str) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from httplib2.error import HttpLib2Error

    body = insert_body(
        title=prep.title,
        description=prep.description,
        category_id=category_id,
        privacy_status=prep.privacy_status,
        publish_at=prep.publish_at,
        made_for_kids=prep.made_for_kids,
        contains_synthetic_media=prep.synthetic,
        language=prep.language,
    )
    media = MediaFileUpload(prep.path, mimetype="video/mp4", resumable=True, chunksize=_CHUNK)
    request = client.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=bool(prep.notify),
    )
    if prep.action == "resume" and prep.session_uri:
        request.resumable_uri = prep.session_uri
        # The client queries the uploaded byte range before sending more bytes.
        request._in_error_state = True
    else:
        uri = _start_session(request)
        _update(prep.publish_id, session_uri=uri)
        prep.session_uri = uri
    response = None
    attempt = 0
    while response is None:
        try:
            _status, response = request.next_chunk(num_retries=0)
        except HttpError as exc:
            if not _retry_http(exc, attempt):
                _raise_http(exc)
            attempt += 1
            time.sleep(_UPLOAD_DELAYS[attempt])
            continue
        except (ConnectionError, TimeoutError, OSError, HttpLib2Error) as exc:
            if attempt + 1 >= len(_UPLOAD_DELAYS):
                raise RetryableUpload(scrub_text(str(exc))) from exc
            attempt += 1
            time.sleep(_UPLOAD_DELAYS[attempt])
    video_id = str((response or {}).get("id") or "")
    if not video_id:
        raise FatalUpload("uploadFailed", "YouTube did not return a video id")
    return video_id


def _start_session(request) -> str:
    """POST the metadata only, then return the resumable session URI. No media bytes."""
    from googleapiclient.errors import HttpError
    from httplib2.error import HttpLib2Error

    http = request.http
    size = "*" if request.resumable.size() is None else str(request.resumable.size())
    headers = dict(request.headers or {})
    headers["X-Upload-Content-Type"] = request.resumable.mimetype()
    if size != "*":
        headers["X-Upload-Content-Length"] = size
    headers["Content-Length"] = str(request.body_size)
    try:
        resp, content = http.request(request.uri, method=request.method, body=request.body, headers=headers)
    except (ConnectionError, TimeoutError, OSError, HttpLib2Error) as exc:
        raise RetryableUpload(scrub_text(str(exc))) from exc
    location = resp["location"] if int(resp.status) == 200 and "location" in resp else ""
    if location:
        request.resumable_uri = location
        return location
    _raise_http(HttpError(resp, content))
    raise RetryableUpload("YouTube did not start an upload session")


def _poll(client, prep: Prep) -> str:
    from googleapiclient.errors import HttpError

    if not prep.video_id:
        raise FatalUpload("uploadFailed", "Missing YouTube video id")
    for delay in _POLL_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            payload = client.videos().list(part="status,processingDetails", id=prep.video_id).execute()
        except HttpError as exc:
            status, _reasons, _message = _parts(exc)
            if classify_api_error(status, _reasons) == "retry":
                return "poll_again"
            _raise_http(exc)
        item = (payload.get("items") or [None])[0] or {}
        status_body = item.get("status") or {}
        details = item.get("processingDetails") or {}
        mapped, code = map_processing(
            upload_status=status_body.get("uploadStatus"),
            processing_status=details.get("processingStatus"),
            failure_reason=status_body.get("failureReason"),
            rejection_reason=status_body.get("rejectionReason"),
            processing_failure_reason=details.get("processingFailureReason"),
            publish_at=prep.publish_at,
            now=datetime.now(timezone.utc),
        )
        if mapped in {"scheduled", "live"}:
            _finish(prep.publish_id, mapped)
            _log(prep.publish_id, prep.video_id, mapped)
            return mapped
        if mapped == "failed":
            _finish(prep.publish_id, "failed", code or "processingFailed", code or "YouTube rejected the video")
            _log(prep.publish_id, prep.video_id, code or "processingFailed")
            return "failed"
    if datetime.now(timezone.utc) - prep.created_at > _PROCESSING_LIMIT:
        _finish(
            prep.publish_id,
            "failed",
            "PROCESSING_TIMEOUT",
            "YouTube is still processing this video. Check YouTube Studio.",
        )
        return "failed"
    _update(prep.publish_id, status="processing")
    return "poll_again"


def _ensure_category(client) -> str:
    from googleapiclient.errors import HttpError

    category_id = get_settings().youtube_category_id.strip() or "22"
    if cached_category_check(category_id):
        return category_id
    try:
        payload = client.videoCategories().list(part="id", id=category_id, regionCode="US").execute()
    except HttpError as exc:
        status, _reasons, _message = _parts(exc)
        if status == 403:
            remember_category_check(category_id, "unverified")
            return category_id
        _raise_http(exc)
        raise
    if not (payload.get("items") or []):
        raise FatalUpload(
            "invalidCategoryId",
            f"YOUTUBE_CATEGORY_ID {category_id} is not valid for this channel. Change it and post again.",
        )
    remember_category_check(category_id, "ok")
    return category_id


def _retry_http(exc, attempt: int) -> bool:
    status, reasons, _message = _parts(exc)
    return classify_api_error(status, reasons) == "retry" and attempt + 1 < len(_UPLOAD_DELAYS)


def _raise_http(exc) -> None:
    status, reasons, message = _parts(exc)
    kind = classify_api_error(status, reasons)
    code = reasons[0] if reasons else f"HTTP_{status}"
    if kind == "quota_stop":
        raise QuotaStop(code, QUOTA_MESSAGE)
    if kind == "retry":
        raise RetryableUpload(message)
    raise FatalUpload(code, message)


def _parts(exc) -> tuple[int, list[str], str]:
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    message = scrub_text(str(getattr(exc, "reason", None) or exc))[:500]
    reasons: list[str] = []
    content = getattr(exc, "content", b"") or b""
    try:
        payload = json.loads(content.decode() if isinstance(content, bytes) else content)
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        if error.get("message"):
            message = scrub_text(str(error["message"]))[:500]
        for item in error.get("errors") or []:
            if isinstance(item, dict) and item.get("reason"):
                reasons.append(str(item["reason"]))
    return status, reasons, message


def _export_file(export: Optional[ClipExport]) -> Optional[Path]:
    if export is None or export.status != "ready" or not export.path:
        return None
    path = get_settings().data_path / export.path
    return path if path.is_file() else None


def _stop_queued(project_id: str, publish_id: str, code: str, message: str) -> None:
    with sync_session() as session:
        rows = (
            session.query(YouTubePublish)
            .filter(
                YouTubePublish.project_id == project_id,
                YouTubePublish.id != publish_id,
                YouTubePublish.status == "queued",
            )
            .all()
        )
        for row in rows:
            _mark(row, "failed", code, message)


def _fail(publish_id: str, code: str, message: str) -> None:
    _update(
        publish_id,
        status="failed",
        error_code=code,
        error_message=scrub_text(message)[:500],
        finished_at=datetime.now(timezone.utc),
    )


def _finish(publish_id: str, status: str, code: Optional[str] = None, message: Optional[str] = None) -> None:
    _update(
        publish_id,
        status=status,
        error_code=code,
        error_message=scrub_text(message)[:500] if message else None,
        finished_at=datetime.now(timezone.utc),
    )


def _mark(row: YouTubePublish, status: str, code: str, message: str) -> None:
    row.status = status
    row.error_code = code
    row.error_message = scrub_text(message)[:500]
    row.finished_at = datetime.now(timezone.utc)


def _update(publish_id: str, **fields) -> None:
    with sync_session() as session:
        row = session.get(YouTubePublish, publish_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)


def _log(publish_id: str, video_id: Optional[str], error_code: Optional[str]) -> None:
    get_logger().info(
        "youtube publish",
        extra={"publish_id": publish_id, "video_id": video_id or "", "error_code": error_code or "", "stage": "publish"},
    )


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
