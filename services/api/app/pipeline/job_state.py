from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import Job, Project


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def update_job(
    session: Session,
    job: Job,
    *,
    stage: str | None = None,
    status: str | None = None,
    progress: float | None = None,
    error: str | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> None:
    if stage is not None:
        job.stage = stage
    if status is not None:
        job.status = status
        if status == "running" and job.started_at is None:
            job.started_at = utcnow()
        if status in {"succeeded", "failed", "cancelled"}:
            job.finished_at = utcnow()
    if progress is not None:
        job.progress = progress
    if error is not None:
        job.error = error
    if checkpoint is not None:
        job.checkpoint = checkpoint
    session.add(job)
    session.commit()


def set_project_status(session: Session, project: Project, status: str) -> None:
    project.status = status
    session.add(project)
    session.commit()
