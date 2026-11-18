import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, false, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TikTokPublish(Base):
    __tablename__ = "tiktok_publishes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    clip_id: Mapped[str] = mapped_column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    export_id: Mapped[str] = mapped_column(String(36), ForeignKey("clip_exports.id", ondelete="CASCADE"))
    export_hash: Mapped[str] = mapped_column(String(64), index=True)
    caption: Mapped[str] = mapped_column(Text, default="")
    privacy_level: Mapped[str] = mapped_column(String(32))
    disable_comment: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    disable_duet: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    disable_stitch: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    brand_content_toggle: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    brand_organic_toggle: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_aigc: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    status: Mapped[str] = mapped_column(String(32), default="queued")
    publish_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    upload_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upload_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tiktok_post_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fail_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
