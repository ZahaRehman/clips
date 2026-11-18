import uuid
from typing import Any, Optional

from sqlalchemy import Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    t_start: Mapped[float] = mapped_column(Float)
    t_end: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    title: Mapped[str] = mapped_column(String(512), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="accepted")
    crop: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    caption_text_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_t_start: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    user_t_end: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    post_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    post_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    post_hashtags: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    post_meta_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    post_meta_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stream_offset_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    project = relationship("Project", back_populates="clips")
    exports = relationship("ClipExport", back_populates="clip", cascade="all, delete-orphan")
