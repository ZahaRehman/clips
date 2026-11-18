import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.db.sync import sync_session
from app.logging_setup import get_logger
from app.models import Project

_TEMP_SUFFIXES = {".part", ".ytdl", ".tmp", ".temp"}
_TEMP_MIN_AGE_SEC = 3600
_FAILED_DIRS = {"proxy", "audio"}


def cleanup_tree(
    root: Path,
    *,
    now: float,
    max_age_sec: float,
    delete_proxy: bool,
    failed_ids: set[str],
) -> dict[str, int]:
    projects = root / "projects"
    removed = 0
    reclaimed = 0
    trimmed: set[str] = set()
    if not projects.is_dir():
        return {"files_removed": 0, "bytes_reclaimed": 0, "failed_projects_trimmed": 0}
    cutoff = now - max_age_sec
    for project in projects.iterdir():
        if not project.is_dir():
            continue
        failed = project.name in failed_ids
        for path in list(project.rglob("*")):
            if not path.is_file():
                continue
            if not _should_delete(path, project, now=now, cutoff=cutoff, delete_proxy=delete_proxy, failed=failed):
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            removed += 1
            reclaimed += size
            if failed:
                trimmed.add(project.name)
    return {
        "files_removed": removed,
        "bytes_reclaimed": reclaimed,
        "failed_projects_trimmed": len(trimmed),
    }


def run_cleanup() -> dict[str, int]:
    settings = get_settings()
    root = Path(settings.data_dir)
    stats = cleanup_tree(
        root,
        now=time.time(),
        max_age_sec=max(0, settings.cleanup_max_age_days) * 86400,
        delete_proxy=settings.cleanup_delete_proxy,
        failed_ids=load_failed_project_ids(),
    )
    get_logger().info(
        "cleanup finished",
        extra={"stage": "cleanup", "bytes_reclaimed": stats["bytes_reclaimed"]},
    )
    return stats


def load_failed_project_ids() -> set[str]:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cleanup_max_age_days)
    ids: set[str] = set()
    with sync_session() as session:
        rows = session.query(Project.id, Project.updated_at).filter(Project.status == "failed").all()
    for project_id, updated in rows:
        if updated is None or _as_utc(updated) < cutoff:
            ids.add(project_id)
    return ids


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _should_delete(
    path: Path,
    project: Path,
    *,
    now: float,
    cutoff: float,
    delete_proxy: bool,
    failed: bool,
) -> bool:
    if path.suffix.lower() in _TEMP_SUFFIXES or path.name.endswith(".part"):
        return path.stat().st_mtime <= now - _TEMP_MIN_AGE_SEC
    rel = path.relative_to(project)
    if delete_proxy and rel.as_posix() == "proxy/proxy.mp4" and path.stat().st_mtime <= cutoff:
        return True
    if failed and rel.parts and rel.parts[0] in _FAILED_DIRS:
        return True
    return False
