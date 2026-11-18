from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.errors import AppError
from app.models import Clip, ClipExport, Project, YouTubePublish
from app.pipeline.clip_plan import clip_bounds
from app.pipeline.paths import remove_clip_files
from app.pipeline.post_text import (
    check_composed,
    check_hashtags,
    check_title,
    compose_description,
    description_body,
)
from app.pipeline.reframe import CROP_MODES
from app.schemas.project import ClipOut, ClipUpdate, CropUpdate, ExportOut
from app.services.clip_view import clip_outs, current_meta_hash, load_words
from app.services.exports import queue_clip_export, to_export_out

router = APIRouter(tags=["clips"])


@router.patch("/clips/{clip_id}", response_model=ClipOut)
async def update_clip(clip_id: str, body: ClipUpdate, db: AsyncSession = Depends(get_db)) -> ClipOut:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise AppError("NOT_FOUND", "Clip not found", status_code=404)
    original_start = clip.t_start
    original_end = clip.t_end
    if body.t_start is not None:
        clip.t_start = body.t_start
        clip.user_t_start = body.t_start
    if body.t_end is not None:
        clip.t_end = body.t_end
        clip.user_t_end = body.t_end
    if body.title is not None:
        clip.title = body.title
    if body.status is not None:
        if body.status not in {"accepted", "rejected"}:
            raise AppError("INVALID_CLIP", "status must be accepted or rejected", status_code=400)
        clip.status = body.status
    if clip.t_end <= clip.t_start:
        raise AppError("INVALID_CLIP", "t_end must be greater than t_start", status_code=400)
    times_sent = body.t_start is not None or body.t_end is not None
    times_changed = abs(clip.t_start - original_start) > 1e-3 or abs(clip.t_end - original_end) > 1e-3
    project = await db.get(Project, clip.project_id)
    if times_sent and times_changed and project:
        min_sec, max_sec = clip_bounds(project)
        duration = clip.t_end - clip.t_start
        if duration < min_sec or duration > max_sec:
            raise AppError(
                "INVALID_CLIP",
                f"Clip duration must be between {min_sec:g} and {max_sec:g}s",
                status_code=400,
            )
    content_changed = False
    if body.crop is not None:
        clip.crop = _crop_payload(body.crop)
        content_changed = True
    if "caption_text_override" in body.model_fields_set:
        text = (body.caption_text_override or "").strip()
        clip.caption_text_override = text or None
        content_changed = True
    if body.model_fields_set & {"post_title", "post_description", "post_hashtags"}:
        await _apply_post_text(clip, body)
    if content_changed or (times_sent and times_changed):
        await _mark_exports_stale(db, clip.id)
    await db.commit()
    await db.refresh(clip)
    return (await clip_outs(db, clip.project_id, [clip]))[0]


@router.delete("/clips/{clip_id}", status_code=204)
async def delete_clip(clip_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise AppError("NOT_FOUND", "Clip not found", status_code=404)
    project_id = clip.project_id
    await db.execute(delete(YouTubePublish).where(YouTubePublish.clip_id == clip_id))
    await db.execute(delete(ClipExport).where(ClipExport.clip_id == clip_id))
    await db.delete(clip)
    await db.commit()
    remove_clip_files(project_id, clip_id)
    return Response(status_code=204)


@router.post("/clips/{clip_id}/export", response_model=ExportOut, status_code=202)
async def enqueue_export(clip_id: str, force: bool = False, db: AsyncSession = Depends(get_db)) -> ExportOut:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise AppError("NOT_FOUND", "Clip not found", status_code=404)
    return await queue_clip_export(db, clip, force=force)


@router.get("/clips/{clip_id}/exports", response_model=list[ExportOut])
async def list_exports(clip_id: str, db: AsyncSession = Depends(get_db)) -> list[ExportOut]:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise AppError("NOT_FOUND", "Clip not found", status_code=404)
    result = await db.execute(
        select(ClipExport).where(ClipExport.clip_id == clip_id).order_by(ClipExport.created_at.desc())
    )
    return [to_export_out(row, clip.project_id) for row in result.scalars().all()]


def _crop_payload(crop: CropUpdate) -> dict:
    if crop.mode not in CROP_MODES:
        raise AppError(
            "INVALID_CROP",
            "crop.mode must be face, center, left, right, custom, or full",
            status_code=400,
        )
    if crop.mode != "custom":
        return {"mode": crop.mode}
    if crop.x is None or crop.y is None:
        raise AppError("INVALID_CROP", "custom crop needs x and y", status_code=400)
    payload: dict = {"mode": "custom", "x": crop.x, "y": crop.y}
    if crop.w is not None:
        payload["w"] = crop.w
    if crop.h is not None:
        payload["h"] = crop.h
    for key, value in payload.items():
        if key == "mode":
            continue
        if value < 0 or value > 1:
            raise AppError("INVALID_CROP", f"crop.{key} must be between 0 and 1", status_code=400)
    if payload.get("w") == 0 or payload.get("h") == 0:
        raise AppError("INVALID_CROP", "custom crop width and height must be greater than 0", status_code=400)
    return payload


async def _apply_post_text(clip: Clip, body: ClipUpdate) -> None:
    """User edits win: validate without silently trimming, then pin the draft to the current clip text."""
    if body.post_title is not None:
        clip.post_title = check_title(body.post_title)
    if body.post_description is not None or body.post_hashtags is not None:
        tags = check_hashtags(body.post_hashtags) if body.post_hashtags is not None else list(clip.post_hashtags or [])
        text = (
            body.post_description
            if body.post_description is not None
            else description_body(clip.post_description, clip.post_hashtags)
        )
        clip.post_description = check_composed(compose_description(text, tags))
        clip.post_hashtags = tags
    clip.post_meta_source = "user"
    clip.post_meta_hash = current_meta_hash(clip, await load_words(clip.project_id))


async def _mark_exports_stale(db: AsyncSession, clip_id: str) -> None:
    result = await db.execute(
        select(ClipExport).where(ClipExport.clip_id == clip_id, ClipExport.status == "ready")
    )
    for row in result.scalars().all():
        row.status = "pending"
