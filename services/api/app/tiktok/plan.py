"""Pure TikTok post planning: captions, chunks, and the init-or-not decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.errors import AppError

FIVE_MB = 5 * 1024 * 1024
SIXTY_FOUR_MB = 64 * 1024 * 1024
CHUNK_OVER_64 = 10_000_000  # TikTok's published example, not 10 MiB
LAST_CHUNK_MAX = 128 * 1024 * 1024
MAX_CLIP_SEC = 180
CAPTION_MAX_UNITS = 2200
ACTIVE_STATUSES = frozenset({"queued", "uploading", "processing", "posted"})
INIT_GAP_SEC = 10.0


@dataclass(frozen=True)
class ChunkPlan:
    chunk_size: int
    total_chunk_count: int


@dataclass(frozen=True)
class CreatorRules:
    privacy_level_options: list[str]
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_video_post_duration_sec: int


@dataclass(frozen=True)
class ClipPost:
    clip_id: str
    export_id: str | None
    export_hash: str | None
    export_status: str | None
    duration_sec: float | None
    frame_rate: float | None
    post_title: str | None
    post_description: str | None
    active_publish_id: str | None = None


@dataclass(frozen=True)
class PlannedPost:
    action: str
    clip_id: str
    export_id: str
    export_hash: str
    caption: str
    existing_id: str | None
    disable_comment: bool
    disable_duet: bool
    disable_stitch: bool


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def snapshot_caption(title: str | None, description: str | None) -> str:
    head = (title or "").strip()
    body = (description or "").strip()
    if head and body:
        return f"{head}\n{body}"
    return head or body


def post_info_title(caption: str) -> str | None:
    """The exact string sent as post_info.title. Empty captions omit the field."""
    return caption if caption else None


def chunk_plan(size: int) -> ChunkPlan:
    if size <= 0:
        raise ValueError("empty file")
    if size <= SIXTY_FOUR_MB:
        return ChunkPlan(chunk_size=size, total_chunk_count=1)
    count = size // CHUNK_OVER_64
    if count < 1 or count > 1000:
        raise ValueError("chunk count out of range")
    last = size - (count - 1) * CHUNK_OVER_64
    if last > LAST_CHUNK_MAX:
        raise ValueError("last chunk exceeds 128 MB")
    return ChunkPlan(chunk_size=CHUNK_OVER_64, total_chunk_count=count)


def put_spans(size: int, plan: ChunkPlan) -> list[tuple[int, int]]:
    """Inclusive byte ranges. The last span absorbs the remainder."""
    if plan.total_chunk_count == 1:
        return [(0, size - 1)]
    spans: list[tuple[int, int]] = []
    for index in range(plan.total_chunk_count - 1):
        start = index * plan.chunk_size
        spans.append((start, start + plan.chunk_size - 1))
    spans.append(((plan.total_chunk_count - 1) * plan.chunk_size, size - 1))
    return spans


def parse_frame_rate(raw: str) -> float:
    text = (raw or "").strip()
    if not text or text == "0/0":
        return 0.0
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            den_f = float(den)
            if den_f == 0:
                return 0.0
            return float(num) / den_f
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def frame_rate_allowed(rate: float) -> bool:
    return 23 <= rate <= 60


def next_publish_step(
    *,
    publish_id: str | None,
    status: str,
    upload_started_at: datetime | None,
    now: datetime,
) -> str:
    """init, resume, poll, or expired. A stored publish_id never returns init."""
    if not publish_id:
        return "init"
    if status == "processing":
        return "poll"
    if upload_started_at is not None and now - upload_started_at <= timedelta(hours=1):
        return "resume"
    return "expired"


def seconds_before_init(last_init_at: float | None, now: float) -> float:
    if last_init_at is None:
        return 0.0
    return max(0.0, INIT_GAP_SEC - (now - last_init_at))


def plan_posts(
    clips: list[ClipPost],
    *,
    privacy_level: str,
    allow_comment: bool,
    allow_duet: bool,
    allow_stitch: bool,
    brand_content: bool,
    rules: CreatorRules,
    watermark_on: bool,
    rights_confirmed: bool,
    music_confirmed: bool,
) -> list[PlannedPost]:
    if not rights_confirmed:
        raise AppError("RIGHTS_REQUIRED", "Confirm you have the right to post these videos.", status_code=400)
    if not music_confirmed:
        raise AppError(
            "MUSIC_REQUIRED",
            "Confirm TikTok's Music Usage Confirmation before posting.",
            status_code=400,
        )
    if watermark_on:
        raise AppError(
            "WATERMARK_PRESENT",
            "Turn WATERMARK_TEXT and WATERMARK_IMAGE off, then export again. TikTok rejects a burned-in watermark.",
            status_code=400,
        )
    if privacy_level not in rules.privacy_level_options:
        raise AppError(
            "privacy_level_option_mismatch",
            "That privacy option is not available for this account.",
            status_code=400,
        )
    if brand_content and privacy_level == "SELF_ONLY":
        raise AppError(
            "BRAND_CONTENT_SELF_ONLY",
            "Branded content cannot be combined with Only me.",
            status_code=400,
        )
    flags = (
        (not allow_comment) or rules.comment_disabled,
        (not allow_duet) or rules.duet_disabled,
        (not allow_stitch) or rules.stitch_disabled,
    )
    planned: list[PlannedPost] = []
    for clip in clips:
        planned.append(_one(clip, rules, flags))
    return planned


def _one(clip: ClipPost, rules: CreatorRules, flags: tuple[bool, bool, bool]) -> PlannedPost:
    if clip.export_status != "ready" or not clip.export_id or not clip.export_hash:
        raise AppError("EXPORT_NOT_READY", "Each selected clip needs a finished export.", status_code=400)
    caption = snapshot_caption(clip.post_title, clip.post_description)
    if utf16_len(caption) > CAPTION_MAX_UNITS:
        raise AppError("CAPTION_TOO_LONG", "A caption is over TikTok's 2200-character limit.", status_code=400)
    duration = clip.duration_sec if clip.duration_sec is not None else -1
    limit = min(MAX_CLIP_SEC, rules.max_video_post_duration_sec or MAX_CLIP_SEC)
    if duration <= 0 or duration > limit:
        raise AppError(
            "CLIP_TOO_LONG",
            f"A clip is longer than {limit:g}s. TikTok will not take it.",
            status_code=400,
        )
    rate = clip.frame_rate if clip.frame_rate is not None else 0
    if not frame_rate_allowed(rate):
        raise AppError(
            "FRAME_RATE",
            "A clip is outside TikTok's 23–60 fps window. Export again from a source in that range.",
            status_code=400,
        )
    return PlannedPost(
        action="reuse" if clip.active_publish_id else "create",
        clip_id=clip.clip_id,
        export_id=clip.export_id,
        export_hash=clip.export_hash,
        caption=caption,
        existing_id=clip.active_publish_id,
        disable_comment=flags[0],
        disable_duet=flags[1],
        disable_stitch=flags[2],
    )
