from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TikTokPublishIn(BaseModel):
    clip_ids: list[str] = Field(min_length=1)
    privacy_level: str
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    brand_organic: bool = False
    brand_content: bool = False
    is_aigc: bool = False
    rights_confirmed: bool
    music_confirmed: bool


class TikTokPublishOut(BaseModel):
    id: str
    project_id: str
    clip_id: str
    export_id: str
    caption: str
    privacy_level: str
    disable_comment: bool
    disable_duet: bool
    disable_stitch: bool
    brand_content_toggle: bool
    brand_organic_toggle: bool
    is_aigc: bool
    status: str
    publish_id: Optional[str] = None
    tiktok_post_id: Optional[str] = None
    fail_reason: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TikTokPublishBatchOut(BaseModel):
    publishes: list[TikTokPublishOut]


class TikTokStatusOut(BaseModel):
    configured: bool
    connected: bool
    display_name: Optional[str] = None


class TikTokCreatorOut(BaseModel):
    nickname: Optional[str] = None
    username: Optional[str] = None
    privacy_level_options: list[str] = []
    comment_disabled: bool = False
    duet_disabled: bool = False
    stitch_disabled: bool = False
    max_video_post_duration_sec: int = 180
    can_post: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
