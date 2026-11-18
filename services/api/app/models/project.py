import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(64), default="created")
    source_type: Mapped[str] = mapped_column(String(32), default="upload")
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    target_clip_count: Mapped[int] = mapped_column(Integer, default=8)
    target_duration_sec: Mapped[int] = mapped_column(Integer, default=45)
    autopilot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    jobs = relationship("Job", back_populates="project", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    transcripts = relationship("Transcript", back_populates="project", cascade="all, delete-orphan")
    clips = relationship("Clip", back_populates="project", cascade="all, delete-orphan")
    live_windows = relationship("LiveWindow", back_populates="project", cascade="all, delete-orphan")
