import shutil
from pathlib import Path

from app.config import get_settings
from app.errors import AppError


def ensure_free_disk(extra_bytes: int = 0) -> None:
    """Fail before ingest if DATA_DIR cannot hold the source plus headroom."""
    settings = get_settings()
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    needed = max(0, settings.min_free_bytes) + max(0, extra_bytes)
    if needed <= 0:
        return
    free = shutil.disk_usage(root).free
    if free < needed:
        raise AppError(
            "DISK_FULL",
            f"Not enough free disk ({free} bytes free, need at least {needed}). "
            "Free space or run POST /api/v1/admin/cleanup.",
            status_code=507,
        )
