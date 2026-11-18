import json
import threading

from app.logging_setup import get_logger
from app.storage import get_storage
from app.storage.local import LocalStorage

_LOCK = threading.Lock()
_KEY = "_metrics.json"


def record_job(status: str) -> None:
    if status not in {"succeeded", "failed"}:
        return
    try:
        storage = get_storage()
        if not isinstance(storage, LocalStorage):
            return
        with _LOCK:
            counts = _read(storage)
            counts[status] = int(counts.get(status, 0)) + 1
            path = storage.local_path(_KEY)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name("_metrics.json.writing")
            tmp.write_text(json.dumps(counts), encoding="utf-8")
            tmp.replace(path)
    except Exception:
        get_logger().info("metrics write failed", extra={"stage": "metrics"})


def snapshot() -> dict[str, int]:
    try:
        storage = get_storage()
        if not isinstance(storage, LocalStorage):
            return {"jobs_succeeded": 0, "jobs_failed": 0}
        counts = _read(storage)
    except Exception:
        counts = {}
    return {
        "jobs_succeeded": int(counts.get("succeeded", 0)),
        "jobs_failed": int(counts.get("failed", 0)),
    }


def _read(storage: LocalStorage) -> dict[str, int]:
    path = storage.local_path(_KEY)
    if not path.is_file():
        return {"succeeded": 0, "failed": 0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"succeeded": 0, "failed": 0}
    if not isinstance(raw, dict):
        return {"succeeded": 0, "failed": 0}
    return {"succeeded": int(raw.get("succeeded", 0)), "failed": int(raw.get("failed", 0))}
