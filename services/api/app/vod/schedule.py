"""Chunk rows stored on the project, and when the chunked project is finished."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Clip, Project
from app.pipeline.job_state import set_project_status

FETCH_QUEUE = "arq:fetcher"


def chunk_list(project: Project) -> list[dict[str, Any]]:
    raw = (project.source_meta or {}).get("chunks")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def is_chunked(project: Project) -> bool:
    return bool((project.source_meta or {}).get("chunked"))


def write_chunks(project: Project, chunks: list[dict[str, Any]]) -> None:
    meta = dict(project.source_meta or {})
    meta["chunks"] = chunks
    project.source_meta = meta


def set_chunk_status(project: Project, index: int, status: str, error: str = "") -> None:
    chunks = chunk_list(project)
    for item in chunks:
        if int(item.get("index") or 0) == index:
            item["status"] = status
            if error:
                item["error"] = error[:500]
            elif "error" in item and status != "failed":
                item.pop("error", None)
            break
    write_chunks(project, chunks)


def chunk_counts(project: Project) -> dict[str, int] | None:
    if not is_chunked(project):
        return None
    chunks = chunk_list(project)
    counts = {"planned": 0, "fetching": 0, "saved": 0, "ready": 0, "failed": 0, "total": len(chunks)}
    for item in chunks:
        status = str(item.get("status") or "planned")
        if status not in counts:
            status = "planned"
        counts[status] += 1
    return counts


def next_planned(project: Project, after: int) -> int | None:
    for item in chunk_list(project):
        index = int(item.get("index") or 0)
        if index > after and item.get("status") == "planned":
            return index
    return None


def maybe_finish(session: Session, project: Project) -> None:
    """Set the project reviewable when every range has finished or failed."""
    if not is_chunked(project):
        return
    counts = chunk_counts(project) or {}
    if counts.get("planned") or counts.get("fetching") or counts.get("saved"):
        return
    has_clip = session.query(Clip.id).filter(Clip.project_id == project.id).first() is not None
    set_project_status(session, project, "ready_for_review" if has_clip else "done")
