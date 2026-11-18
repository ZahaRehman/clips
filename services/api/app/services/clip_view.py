"""Clip responses for the gallery: post text, stale flag, and latest export in one call."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Clip, ClipExport, LiveWindow
from app.pipeline.post_text import clip_text, description_body, metadata_hash
from app.schemas.project import ClipOut
from app.services.exports import to_export_out


async def load_words(project_id: str) -> list[dict[str, Any]]:
    path = get_settings().data_path / "projects" / project_id / "transcript" / "words.json"

    def read() -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    return await asyncio.to_thread(read)


def current_meta_hash(clip: Clip, words: list[dict[str, Any]]) -> str:
    text = clip_text(words, clip.t_start, clip.t_end, clip.caption_text_override)
    return metadata_hash(text, get_settings().gemini_model)


async def clip_outs(db: AsyncSession, project_id: str, clips: list[Clip]) -> list[ClipOut]:
    if not clips:
        return []
    shared = await load_words(project_id)
    windows = await _windows(db, project_id)
    latest = await _latest_exports(db, [c.id for c in clips])
    outs: list[ClipOut] = []
    for clip in clips:
        words = _words_for_clip(project_id, clip, shared, windows)
        outs.append(_clip_out(clip, words, latest.get(clip.id), project_id))
    return outs


async def _windows(db: AsyncSession, project_id: str) -> list[LiveWindow]:
    result = await db.execute(select(LiveWindow).where(LiveWindow.project_id == project_id))
    return list(result.scalars().all())


def _words_for_clip(
    project_id: str,
    clip: Clip,
    shared: list[dict[str, Any]],
    windows: list[LiveWindow],
) -> list[dict[str, Any]]:
    if clip.stream_offset_sec is None:
        return shared
    target = round(float(clip.stream_offset_sec), 3)
    for window in windows:
        if round(float(window.stream_start_sec), 3) != target:
            continue
        path = (
            get_settings().data_path
            / "projects"
            / project_id
            / "windows"
            / f"{int(window.index):04d}"
            / "transcript"
            / "words.json"
        )
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    return shared


def _clip_out(clip: Clip, words: list[dict[str, Any]], export: ClipExport | None, project_id: str) -> ClipOut:
    out = ClipOut.model_validate(clip)
    out.post_body = description_body(clip.post_description, clip.post_hashtags)
    if clip.post_meta_hash and words:
        out.post_meta_stale = clip.post_meta_hash != current_meta_hash(clip, words)
    if export:
        out.latest_export = to_export_out(export, project_id)
    return out


async def _latest_exports(db: AsyncSession, clip_ids: list[str]) -> dict[str, ClipExport]:
    result = await db.execute(
        select(ClipExport).where(ClipExport.clip_id.in_(clip_ids)).order_by(ClipExport.created_at.desc())
    )
    latest: dict[str, ClipExport] = {}
    for row in result.scalars().all():
        latest.setdefault(row.clip_id, row)
    return latest
