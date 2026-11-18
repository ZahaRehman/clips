"""Create TikTok publish rows. The worker does the upload."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import Clip, ClipExport, Project, TikTokPublish
from app.pipeline.ffmpeg_util import ffprobe
from app.pipeline.watermark import watermark_token
from app.schemas.tiktok import TikTokCreatorOut, TikTokPublishBatchOut, TikTokPublishIn, TikTokPublishOut
from app.services.enqueue import enqueue
from app.tiktok.errors import classify_tiktok, message_for
from app.tiktok.oauth import TikTokAPIError, TikTokDisconnected, configured, connection_view, fetch_creator
from app.tiktok.plan import ACTIVE_STATUSES, ClipPost, CreatorRules, parse_frame_rate, plan_posts
from app.worker_queues import PUBLISH_QUEUE


async def creator_view() -> TikTokCreatorOut:
    if not configured():
        raise AppError("TIKTOK_NOT_CONFIGURED", "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET", status_code=400)
    if not connection_view()["connected"]:
        raise AppError("TIKTOK_DISCONNECTED", message_for("TIKTOK_DISCONNECTED"), status_code=400)
    try:
        data = await asyncio.to_thread(fetch_creator)
    except TikTokDisconnected as exc:
        raise AppError("TIKTOK_DISCONNECTED", message_for("TIKTOK_DISCONNECTED"), status_code=400) from exc
    except TikTokAPIError as exc:
        kind = classify_tiktok(exc.code)
        if kind in {"stop_batch", "disconnected"}:
            return TikTokCreatorOut(can_post=False, error_code=exc.code, error_message=message_for(exc.code, exc.message))
        raise AppError(exc.code or "TIKTOK_ERROR", message_for(exc.code, exc.message), status_code=400) from exc
    options = [str(item) for item in (data.get("privacy_level_options") or [])]
    return TikTokCreatorOut(
        nickname=_text(data.get("creator_nickname")),
        username=_text(data.get("creator_username")),
        privacy_level_options=options,
        comment_disabled=bool(data.get("comment_disabled")),
        duet_disabled=bool(data.get("duet_disabled")),
        stitch_disabled=bool(data.get("stitch_disabled")),
        max_video_post_duration_sec=int(data.get("max_video_post_duration_sec") or 180),
        can_post=True,
    )


async def create_tiktok_publish(db: AsyncSession, project_id: str, body: TikTokPublishIn) -> TikTokPublishBatchOut:
    if not configured():
        raise AppError(
            "TIKTOK_NOT_CONFIGURED",
            "TikTok posting is off until TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET are set",
            status_code=400,
        )
    if not connection_view()["connected"]:
        raise AppError("TIKTOK_DISCONNECTED", message_for("TIKTOK_DISCONNECTED"), status_code=400)
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("NOT_FOUND", "Project not found", status_code=404)
    creator = await creator_view()
    if not creator.can_post:
        raise AppError(
            creator.error_code or "TIKTOK_ERROR",
            creator.error_message or "TikTok will not take posts from this account right now.",
            status_code=400,
        )
    clip_ids = list(dict.fromkeys(body.clip_ids))
    result = await db.execute(select(Clip).where(Clip.project_id == project_id, Clip.id.in_(clip_ids)))
    found = {clip.id: clip for clip in result.scalars().all()}
    if len(found) != len(clip_ids):
        raise AppError("NOT_FOUND", "A selected clip is not in this project", status_code=404)
    exports = await _latest_exports(db, clip_ids)
    hashes = [export.input_hash for export in exports.values() if export.input_hash]
    existing = await _rows_for_hashes(db, hashes)
    rules = CreatorRules(
        privacy_level_options=creator.privacy_level_options,
        comment_disabled=creator.comment_disabled,
        duet_disabled=creator.duet_disabled,
        stitch_disabled=creator.stitch_disabled,
        max_video_post_duration_sec=creator.max_video_post_duration_sec,
    )
    posts = [
        _clip_post(found[clip_id], exports.get(clip_id), existing)
        for clip_id in clip_ids
    ]
    planned = plan_posts(
        posts,
        privacy_level=body.privacy_level,
        allow_comment=body.allow_comment,
        allow_duet=body.allow_duet,
        allow_stitch=body.allow_stitch,
        brand_content=body.brand_content,
        rules=rules,
        watermark_on=bool(watermark_token()),
        rights_confirmed=body.rights_confirmed,
        music_confirmed=body.music_confirmed,
    )
    rows: list[TikTokPublish] = []
    enqueue_ids: list[str] = []
    for item in planned:
        if item.action == "reuse" and item.existing_id:
            row = await db.get(TikTokPublish, item.existing_id)
            if row:
                rows.append(row)
            continue
        row = TikTokPublish(
            id=str(uuid.uuid4()),
            project_id=project_id,
            clip_id=item.clip_id,
            export_id=item.export_id,
            export_hash=item.export_hash,
            caption=item.caption,
            privacy_level=body.privacy_level,
            disable_comment=item.disable_comment,
            disable_duet=item.disable_duet,
            disable_stitch=item.disable_stitch,
            brand_content_toggle=body.brand_content,
            brand_organic_toggle=body.brand_organic,
            is_aigc=body.is_aigc,
            status="queued",
        )
        db.add(row)
        rows.append(row)
        enqueue_ids.append(row.id)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    await _enqueue_all(db, rows, enqueue_ids)
    return TikTokPublishBatchOut(publishes=[to_out(row) for row in rows])


async def list_tiktok_publishes(db: AsyncSession, project_id: str) -> list[TikTokPublishOut]:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("NOT_FOUND", "Project not found", status_code=404)
    result = await db.execute(
        select(TikTokPublish).where(TikTokPublish.project_id == project_id).order_by(TikTokPublish.created_at.desc())
    )
    return [to_out(row) for row in result.scalars().all()]


def to_out(row: TikTokPublish) -> TikTokPublishOut:
    return TikTokPublishOut(
        id=row.id,
        project_id=row.project_id,
        clip_id=row.clip_id,
        export_id=row.export_id,
        caption=row.caption,
        privacy_level=row.privacy_level,
        disable_comment=row.disable_comment,
        disable_duet=row.disable_duet,
        disable_stitch=row.disable_stitch,
        brand_content_toggle=row.brand_content_toggle,
        brand_organic_toggle=row.brand_organic_toggle,
        is_aigc=row.is_aigc,
        status=row.status,
        publish_id=row.publish_id,
        tiktok_post_id=row.tiktok_post_id,
        fail_reason=row.fail_reason,
        error_message=row.error_message,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _clip_post(clip: Clip, export: ClipExport | None, existing: list[TikTokPublish]) -> ClipPost:
    active = _active(existing, export.input_hash if export else None)
    rate = _frame_rate(export)
    return ClipPost(
        clip_id=clip.id,
        export_id=export.id if export else None,
        export_hash=export.input_hash if export else None,
        export_status=export.status if export else None,
        duration_sec=export.duration_sec if export else None,
        frame_rate=rate,
        post_title=clip.post_title,
        post_description=clip.post_description,
        active_publish_id=active.id if active else None,
    )


def _frame_rate(export: ClipExport | None) -> float | None:
    if export is None or export.status != "ready" or not export.path:
        return None
    path = get_settings().data_path / export.path
    if not path.is_file():
        return None
    probe = ffprobe(path)
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == "video":
            raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "")
            return parse_frame_rate(raw)
    return 0.0


def _active(rows: list[TikTokPublish], export_hash: str | None) -> TikTokPublish | None:
    if not export_hash:
        return None
    matching = [row for row in rows if row.export_hash == export_hash and row.status in ACTIVE_STATUSES]
    matching.sort(key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return matching[0] if matching else None


async def _latest_exports(db: AsyncSession, clip_ids: list[str]) -> dict[str, ClipExport]:
    result = await db.execute(
        select(ClipExport).where(ClipExport.clip_id.in_(clip_ids)).order_by(ClipExport.created_at.desc())
    )
    latest: dict[str, ClipExport] = {}
    for row in result.scalars().all():
        latest.setdefault(row.clip_id, row)
    return latest


async def _rows_for_hashes(db: AsyncSession, hashes: list[str]) -> list[TikTokPublish]:
    if not hashes:
        return []
    result = await db.execute(select(TikTokPublish).where(TikTokPublish.export_hash.in_(hashes)))
    return list(result.scalars().all())


async def _enqueue_all(db: AsyncSession, rows: list[TikTokPublish], enqueue_ids: list[str]) -> None:
    sent: list[str] = []
    try:
        for publish_id in enqueue_ids:
            await enqueue("run_tiktok_publish", publish_id, queue=PUBLISH_QUEUE)
            sent.append(publish_id)
    except Exception as exc:
        now = datetime.now(timezone.utc)
        for row in rows:
            if row.id in enqueue_ids and row.id not in sent:
                row.status = "failed"
                row.fail_reason = "PUBLISH_QUEUE_FAILED"
                row.error_message = "Could not queue the upload. Check that Redis and the worker are running."
                row.finished_at = now
        await db.commit()
        raise AppError(
            "PUBLISH_QUEUE_FAILED",
            "Could not queue the upload. Check that Redis and the worker are running.",
            status_code=503,
        ) from exc


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
