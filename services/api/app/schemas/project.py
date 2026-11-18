from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class JobOut(BaseModel):
    id: str
    kind: str
    stage: str
    status: str
    progress: float
    error: Optional[str] = None

    model_config = {"from_attributes": True}


class AutopilotSummary(BaseModel):
    requested: int
    planned: Optional[int] = None
    plan_reason: str = ""
    rendered: int = 0
    failed: int = 0
    render_current: int = 0
    render_total: int = 0
    render_errors: list[dict[str, str]] = Field(default_factory=list)
    provider_used: Optional[str] = None
    llm_error: Optional[str] = None
    metadata_source: Optional[str] = None
    metadata_error: Optional[str] = None
    metadata_error_message: Optional[str] = None


class LiveStatus(BaseModel):
    mode: str
    state: str
    behind_sec: float
    windows_ready: int
    windows_failed: int
    windows_saved: int = 0


class ChunkStatus(BaseModel):
    planned: int = 0
    fetching: int = 0
    saved: int = 0
    ready: int = 0
    failed: int = 0
    total: int = 0
    error: Optional[str] = None


class ProjectOut(BaseModel):
    id: str
    title: str
    status: str
    source_type: str
    created_at: datetime
    source_meta: Optional[dict[str, Any]] = None
    autopilot: bool = False
    target_clip_count: int = 0
    framing: str = "face"
    latest_job: Optional[JobOut] = None
    autopilot_summary: Optional[AutopilotSummary] = None
    live: Optional[LiveStatus] = None
    chunks: Optional[ChunkStatus] = None

    model_config = {"from_attributes": True}


class ProjectCreated(BaseModel):
    id: str
    status: str


class ProjectCreateUrl(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    disclaimer_accepted: bool
    title: Optional[str] = None
    autopilot: bool = False
    clip_count: Optional[int] = None
    framing: Optional[str] = None


class ProjectCreateLive(BaseModel):
    url: Optional[str] = None
    folder: Optional[str] = None
    disclaimer_accepted: bool = False
    title: Optional[str] = None
    autopilot: bool = False
    clip_count: Optional[int] = None
    framing: Optional[str] = None


class FramingUpdate(BaseModel):
    framing: str


class ReprocessBody(BaseModel):
    from_stage: str


class ClipCreate(BaseModel):
    t_start: float = Field(..., ge=0)
    t_end: float = Field(..., gt=0)
    title: Optional[str] = None


class CropUpdate(BaseModel):
    mode: str
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None


class ClipUpdate(BaseModel):
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    title: Optional[str] = None
    status: Optional[str] = None
    crop: Optional[CropUpdate] = None
    caption_text_override: Optional[str] = None
    post_title: Optional[str] = None
    post_description: Optional[str] = Field(None, description="Body text; hashtags are appended on save")
    post_hashtags: Optional[list[str]] = None


class ExportOut(BaseModel):
    id: str
    clip_id: str
    preset: str
    path: str
    status: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration_sec: Optional[float] = None
    error: Optional[str] = None
    download_url: Optional[str] = None

    model_config = {"from_attributes": True}


class ClipOut(BaseModel):
    id: str
    project_id: str
    t_start: float
    t_end: float
    title: str
    status: str
    rank: int = 0
    score: float = 0
    reason: str = ""
    crop: Optional[dict[str, Any]] = None
    caption_text_override: Optional[str] = None
    post_title: Optional[str] = None
    post_description: Optional[str] = Field(None, description="Final composed text: body, blank line, hashtags")
    post_body: str = ""
    post_hashtags: list[str] = Field(default_factory=list)
    post_meta_source: Optional[str] = None
    post_meta_stale: bool = False
    stream_offset_sec: Optional[float] = None
    latest_export: Optional[ExportOut] = None

    model_config = {"from_attributes": True}

    @field_validator("post_hashtags", mode="before")
    @classmethod
    def _none_to_list(cls, value: Any) -> Any:
        return value or []
