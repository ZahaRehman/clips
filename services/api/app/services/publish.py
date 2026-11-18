"""Create publish rows and enqueue one worker job per clip."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import Clip, ClipExport, Project, YouTubePublish
from app.schemas.youtube import PublishBatchOut, PublishIn, PublishOut
from app.services.enqueue import enqueue
from app.youtube.errors import DISCONNECTED_MESSAGE
from app.youtube.oauth import configured, connection_view, remember_made_for_kids
from app.youtube.schedule import ACTIVE_STATUSES, ClipCandidate, plan_batch
from app.worker_queues import PUBLISH_QUEUE


async def create_publish(db: AsyncSession, project_id: str, body: PublishIn) -> PublishBatchOut:
    if not configured():
        raise AppError(
            "YOUTUBE_NOT_CONFIGURED",
            "YouTube posting is off until YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are set",
            status_code=400,
        )
    if not connection_view()["connected"]:
        raise AppError("YOUTUBE_DISCONNECTED", DISCONNECTED_MESSAGE, status_code=400)
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("NOT_FOUND", "Project not found", status_code=404)

    clip_ids = list(dict.fromkeys(body.clip_ids))
    result = await db.execute(select(Clip).where(Clip.project_id == project_id, Clip.id.in_(clip_ids)))
    found = {clip.id: clip for clip in result.scalars().all()}
    if len(found) != len(clip_ids):
        raise AppError("NOT_FOUND", "A selected clip is not in this project", status_code=404)
    ordered = [found[clip_id] for clip_id in clip_ids]
    exports = await _latest_exports(db, clip_ids)
    hashes = [export.input_hash for export in exports.values() if export.input_hash]
    existing = await _publishes_for_hashes(db, hashes)
    now = datetime.now(timezone.utc)
    candidates = [_candidate(clip, exports.get(clip.id), existing) for clip in ordered]
    plan = plan_batch(
        candidates,
        privacy=body.privacy,
        start_at=body.schedule.start_at if body.schedule else None,
        every_minutes=body.schedule.every_minutes if body.schedule else None,
        made_for_kids=body.made_for_kids,
        rights_confirmed=body.rights_confirmed,
        now=now,
    )
    remember_made_for_kids(body.made_for_kids)

    rows: list[YouTubePublish] = []
    enqueue_ids: list[str] = []
    for planned in plan.rows:
        if planned.action == "reuse":
            row = await db.get(YouTubePublish, planned.existing_id)
            if row:
                rows.append(row)
            continue
        if planned.action in {"retry_same", "retry_fresh"}:
            row = await db.get(YouTubePublish, planned.existing_id)
            if not row:
                raise AppError("NOT_FOUND", "Publish row disappeared", status_code=404)
            if planned.action == "retry_fresh":
                _apply_snapshot(row, planned, body)
                row.session_uri = None
                row.youtube_video_id = None
            row.status = "queued"
            row.error_code = None
            row.error_message = None
            row.finished_at = None
            rows.append(row)
            enqueue_ids.append(row.id)
            continue
        row = YouTubePublish(
            id=str(uuid.uuid4()),
            project_id=project_id,
            clip_id=planned.clip_id,
            export_id=planned.export_id,
            export_hash=planned.export_hash,
            title=planned.title,
            description=planned.description,
            privacy_status=planned.privacy_status,
            publish_at=planned.publish_at,
            notify_subscribers=body.notify_subscribers,
            made_for_kids=body.made_for_kids,
            contains_synthetic_media=False,
            status="queued",
        )
        db.add(row)
        rows.append(row)
        enqueue_ids.append(row.id)

    await db.commit()
    for row in rows:
        await db.refresh(row)
    await _enqueue_all(db, rows, enqueue_ids)
    return PublishBatchOut(publishes=[to_publish_out(row) for row in rows], warnings=plan.warnings)


async def list_publishes(db: AsyncSession, project_id: str) -> list[PublishOut]:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("NOT_FOUND", "Project not found", status_code=404)
    result = await db.execute(
        select(YouTubePublish)
        .where(YouTubePublish.project_id == project_id)
        .order_by(YouTubePublish.created_at.desc())
    )
    return [to_publish_out(row) for row in result.scalars().all()]


def to_publish_out(row: YouTubePublish) -> PublishOut:
    video_id = row.youtube_video_id or None
    return PublishOut(
        id=row.id,
        project_id=row.project_id,
        clip_id=row.clip_id,
        export_id=row.export_id,
        title=row.title,
        description=row.description,
        privacy_status=row.privacy_status,
        publish_at=row.publish_at,
        notify_subscribers=row.notify_subscribers,
        made_for_kids=row.made_for_kids,
        status=row.status,
        youtube_video_id=video_id,
        watch_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


async def _enqueue_all(db: AsyncSession, rows: list[YouTubePublish], enqueue_ids: list[str]) -> None:
    sent: list[str] = []
    ids = [publish_id for publish_id in enqueue_ids if publish_id]
    if len(ids) != len(enqueue_ids):
        now = datetime.now(timezone.utc)
        for row in rows:
            if not row.id:
                row.status = "failed"
                row.error_code = "PUBLISH_QUEUE_FAILED"
                row.error_message = "The upload never started. Nothing was sent to YouTube."
                row.finished_at = now
        await db.commit()
    try:
        for publish_id in ids:
            await enqueue("run_publish", publish_id, queue=PUBLISH_QUEUE)
            sent.append(publish_id)
    except Exception as exc:
        now = datetime.now(timezone.utc)
        for row in rows:
            if row.id in ids and row.id not in sent:
                row.status = "failed"
                row.error_code = "PUBLISH_QUEUE_FAILED"
                row.error_message = "Could not queue the upload. Check that Redis and the worker are running."
                row.finished_at = now
        await db.commit()
        raise AppError(
            "PUBLISH_QUEUE_FAILED",
            "Could not queue the upload. Check that Redis and the worker are running.",
            status_code=503,
        ) from exc


def _apply_snapshot(row: YouTubePublish, planned, body: PublishIn) -> None:
    row.export_id = planned.export_id
    row.export_hash = planned.export_hash
    row.title = planned.title
    row.description = planned.description
    row.privacy_status = planned.privacy_status
    row.publish_at = planned.publish_at
    row.notify_subscribers = body.notify_subscribers
    row.made_for_kids = body.made_for_kids
    row.contains_synthetic_media = False


def _candidate(clip: Clip, export: ClipExport | None, existing: list[YouTubePublish]) -> ClipCandidate:
    active, failed = _match(existing, export.input_hash if export else None)
    return ClipCandidate(
        clip_id=clip.id,
        export_id=export.id if export else None,
        export_hash=export.input_hash if export else None,
        export_status=export.status if export else None,
        duration_sec=export.duration_sec if export else None,
        post_title=clip.post_title,
        post_description=clip.post_description,
        active_publish_id=active.id if active else None,
        failed_publish_id=failed.id if failed else None,
        failed_has_session=bool(failed and failed.session_uri),
        failed_video_sent=bool(failed and failed.youtube_video_id),
    )


def _match(
    rows: list[YouTubePublish], export_hash: str | None
) -> tuple[YouTubePublish | None, YouTubePublish | None]:
    if not export_hash:
        return None, None
    matching = [row for row in rows if row.export_hash == export_hash]
    matching.sort(key=lambda row: _stamp(row.created_at), reverse=True)
    active = next((row for row in matching if row.status in ACTIVE_STATUSES), None)
    failed = next((row for row in matching if row.status == "failed"), None)
    return active, failed


async def _latest_exports(db: AsyncSession, clip_ids: list[str]) -> dict[str, ClipExport]:
    result = await db.execute(
        select(ClipExport).where(ClipExport.clip_id.in_(clip_ids)).order_by(ClipExport.created_at.desc())
    )
    latest: dict[str, ClipExport] = {}
    for row in result.scalars().all():
        latest.setdefault(row.clip_id, row)
    return latest


async def _publishes_for_hashes(db: AsyncSession, hashes: list[str]) -> list[YouTubePublish]:
    if not hashes:
        return []
    result = await db.execute(select(YouTubePublish).where(YouTubePublish.export_hash.in_(hashes)))
    return list(result.scalars().all())


def _stamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

