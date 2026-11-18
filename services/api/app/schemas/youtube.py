from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ScheduleIn(BaseModel):
    start_at: datetime
    every_minutes: int = Field(ge=1)


class PublishIn(BaseModel):
    clip_ids: list[str] = Field(min_length=1)
    privacy: str
    schedule: Optional[ScheduleIn] = None
    notify_subscribers: bool = False
    made_for_kids: bool
    rights_confirmed: bool


class PublishOut(BaseModel):
    id: str
    project_id: str
    clip_id: str
    export_id: str
    title: str
    description: str
    privacy_status: str
    publish_at: Optional[datetime] = None
    notify_subscribers: bool
    made_for_kids: bool
    status: str
    youtube_video_id: Optional[str] = None
    watch_url: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PublishBatchOut(BaseModel):
    publishes: list[PublishOut]
    warnings: list[str]


class LiveStreamOut(BaseModel):
    video_id: str
    url: str
    title: str
    channel_title: str
    channel_id: str
    thumbnail_url: str
    concurrent_viewers: Optional[int] = None
    started_at: Optional[str] = None
    own: bool = False


class LiveDiscoverOut(BaseModel):
    enabled: bool
    cached: bool = False
    region: Optional[str] = None
    message: Optional[str] = None
    streams: list[LiveStreamOut] = Field(default_factory=list)


class YouTubeStatusOut(BaseModel):
    configured: bool
    connected: bool
    channel_title: Optional[str] = None
    channel_label: Optional[str] = None
    made_for_kids: Optional[bool] = None
    default_spacing_min: int
