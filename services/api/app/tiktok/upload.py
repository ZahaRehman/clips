"""Direct Post FILE_UPLOAD. publish_id is saved before any media byte is sent."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.db.sync import sync_session
from app.models import ClipExport, TikTokPublish
from app.tiktok.errors import BatchStop, RetryLater, RowFail, TikTokAPIError, message_for, raise_classified
from app.tiktok.oauth import TikTokDisconnected, api_json
from app.tiktok.plan import chunk_plan, next_publish_step, post_info_title, put_spans, seconds_before_init
from app.tiktok.rows import fail_publish, log_publish, mark_failed, stop_queued, update_publish
from app.tiktok.status import poll_publish, remote_offset
from app.tiktok.transfer import put_span

_last_init_at: float | None = None

INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"


@dataclass
class Prep:
    local_id: str
    project_id: str
    path: str
    caption: str
    privacy_level: str
    disable_comment: bool
    disable_duet: bool
    disable_stitch: bool
    brand_content: bool
    brand_organic: bool
    is_aigc: bool
    publish_id: Optional[str]
    upload_url: Optional[str]
    upload_started_at: Optional[datetime]
    status: str
    step: str


def execute_tiktok_publish(publish_id: str) -> str:
    prep = _prepare(publish_id)
    if isinstance(prep, str):
        return prep
    try:
        if prep.step == "expired":
            raise RowFail(
                "UPLOAD_EXPIRED",
                "The TikTok upload link expired. Check the TikTok app before trying again. Cliping did not start a second post.",
            )
        if prep.step == "init":
            _init(prep)
            prep = _reload(prep.local_id)
        if prep.step == "resume":
            _upload(prep)
            prep = _reload(prep.local_id)
        return poll_publish(prep.local_id, prep.publish_id)
    except TikTokDisconnected:
        fail_publish(prep.local_id, "TIKTOK_DISCONNECTED", message_for("TIKTOK_DISCONNECTED"))
        stop_queued(prep.project_id, prep.local_id, "TIKTOK_DISCONNECTED", message_for("TIKTOK_DISCONNECTED"))
        log_publish(prep.publish_id, "TIKTOK_DISCONNECTED")
        return "stopped"
    except BatchStop as exc:
        fail_publish(prep.local_id, exc.code, exc.message)
        stop_queued(prep.project_id, prep.local_id, exc.code, exc.message)
        log_publish(prep.publish_id, exc.code)
        return "stopped"
    except RowFail as exc:
        fail_publish(prep.local_id, exc.code, exc.message)
        log_publish(prep.publish_id, exc.code)
        return "failed"
    except RetryLater:
        log_publish(prep.publish_id, "retry")
        return "retry"


def unfinished_tiktok_ids() -> list[str]:
    with sync_session() as session:
        rows = (
            session.query(TikTokPublish)
            .filter(TikTokPublish.status.in_(("queued", "uploading", "processing")))
            .all()
        )
        return [row.id for row in rows]


def _prepare(local_id: str) -> Prep | str:
    with sync_session() as session:
        row = session.get(TikTokPublish, local_id)
        if row is None:
            return "missing"
        if row.status in {"posted", "failed"}:
            return row.status
        export = session.get(ClipExport, row.export_id)
        path = _export_file(export)
        if path is None:
            mark_failed(row, "EXPORT_MISSING", "The export file is missing. Export the clip again.")
            return "failed"
        now = datetime.now(timezone.utc)
        step = next_publish_step(
            publish_id=row.publish_id,
            status=row.status,
            upload_started_at=_as_utc(row.upload_started_at),
            now=now,
        )
        if step == "poll":
            row.status = "processing"
        elif step != "expired":
            row.status = "uploading"
        return _prep_from(row, path, step)


def _reload(local_id: str) -> Prep:
    with sync_session() as session:
        row = session.get(TikTokPublish, local_id)
        if row is None or not row.publish_id:
            raise RetryLater()
        export = session.get(ClipExport, row.export_id)
        path = _export_file(export)
        if path is None:
            raise RowFail("EXPORT_MISSING", "The export file is missing. Export the clip again.")
        step = "poll" if row.status == "processing" else "resume"
        return _prep_from(row, path, step)


def _prep_from(row: TikTokPublish, path: Path, step: str) -> Prep:
    return Prep(
        local_id=row.id,
        project_id=row.project_id,
        path=str(path),
        caption=row.caption,
        privacy_level=row.privacy_level,
        disable_comment=row.disable_comment,
        disable_duet=row.disable_duet,
        disable_stitch=row.disable_stitch,
        brand_content=row.brand_content_toggle,
        brand_organic=row.brand_organic_toggle,
        is_aigc=row.is_aigc,
        publish_id=row.publish_id,
        upload_url=row.upload_url,
        upload_started_at=_as_utc(row.upload_started_at),
        status=row.status,
        step=step,
    )


def _init(prep: Prep) -> None:
    global _last_init_at
    wait = seconds_before_init(_last_init_at, time.monotonic())
    if wait:
        time.sleep(wait)
    size = Path(prep.path).stat().st_size
    plan = _plan(size)
    info: dict = {
        "privacy_level": prep.privacy_level,
        "disable_duet": prep.disable_duet,
        "disable_comment": prep.disable_comment,
        "disable_stitch": prep.disable_stitch,
        "video_cover_timestamp_ms": 0,
        "brand_content_toggle": prep.brand_content,
        "brand_organic_toggle": prep.brand_organic,
        "is_aigc": prep.is_aigc,
    }
    title = post_info_title(prep.caption)
    if title is not None:
        info["title"] = title
    body = {
        "post_info": info,
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": plan.chunk_size,
            "total_chunk_count": plan.total_chunk_count,
        },
    }
    try:
        _last_init_at = time.monotonic()
        data = api_json("POST", INIT_URL, body)
    except TikTokAPIError as exc:
        raise_classified(exc)
    remote = str(data.get("publish_id") or "")
    upload_url = str(data.get("upload_url") or "")
    if not remote or not upload_url:
        raise RetryLater()
    update_publish(
        prep.local_id,
        publish_id=remote,
        upload_url=upload_url,
        upload_started_at=datetime.now(timezone.utc),
        status="uploading",
    )
    prep.publish_id = remote
    prep.upload_url = upload_url
    log_publish(remote, "init")


def _upload(prep: Prep) -> None:
    upload_url = prep.upload_url
    if not upload_url or not prep.publish_id:
        raise RetryLater()
    size = Path(prep.path).stat().st_size
    plan = _plan(size)
    spans = put_spans(size, plan)
    offset = remote_offset(prep.publish_id)
    for start, end in spans:
        if end < offset:
            continue
        send_from = max(start, offset)
        put_span(upload_url, prep.path, send_from, end, size)
        offset = end + 1
    update_publish(prep.local_id, status="processing")


def _plan(size: int):
    try:
        return chunk_plan(size)
    except ValueError as exc:
        raise RowFail("UPLOAD_REJECTED", "The export file is empty.") from exc


def _export_file(export: Optional[ClipExport]) -> Optional[Path]:
    if export is None or export.status != "ready" or not export.path:
        return None
    path = get_settings().data_path / export.path
    return path if path.is_file() else None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
