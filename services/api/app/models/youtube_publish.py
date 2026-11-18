import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class YouTubePublish(Base):
    __tablename__ = "youtube_publishes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    clip_id: Mapped[str] = mapped_column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    export_id: Mapped[str] = mapped_column(String(36), ForeignKey("clip_exports.id", ondelete="CASCADE"))
    export_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    privacy_status: Mapped[str] = mapped_column(String(16))
    publish_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notify_subscribers: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    made_for_kids: Mapped[bool] = mapped_column(Boolean, default=False)
    contains_synthetic_media: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    status: Mapped[str] = mapped_column(String(32), default="queued")
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    session_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
