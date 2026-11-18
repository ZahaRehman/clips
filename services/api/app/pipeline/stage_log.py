import time
from collections.abc import Callable
from typing import TypeVar

from app.logging_setup import get_logger

T = TypeVar("T")


def call_stage(project_id: str, job_id: str, stage: str, fn: Callable[[], T]) -> T:
    log = get_logger()
    started = time.perf_counter()
    base = {"project_id": project_id, "job_id": job_id, "stage": stage}
    log.info("stage started", extra=base)
    try:
        result = fn()
    except Exception:
        log.info("stage failed", extra={**base, "duration_ms": _ms(started)})
        raise
    log.info("stage finished", extra={**base, "duration_ms": _ms(started)})
    return result


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
