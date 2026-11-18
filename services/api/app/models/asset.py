import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(String(1024))
    checksum: Mapped[str] = mapped_column(String(128), default="")
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    probe: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    project = relationship("Project", back_populates="assets")
