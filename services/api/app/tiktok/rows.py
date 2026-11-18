"""Persist TikTok publish row outcomes. Upload URLs are never logged."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.sync import sync_session
from app.logging_setup import get_logger
from app.models import TikTokPublish
from app.redact import scrub_text


def update_publish(local_id: str, **fields) -> None:
    with sync_session() as session:
        row = session.get(TikTokPublish, local_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)


def fail_publish(local_id: str, code: str, message: str) -> None:
    update_publish(
        local_id,
        status="failed",
        fail_reason=code[:64],
        error_message=scrub_text(message)[:2000],
        finished_at=datetime.now(timezone.utc),
    )


def mark_failed(row: TikTokPublish, code: str, message: str) -> None:
    row.status = "failed"
    row.fail_reason = code[:64]
    row.error_message = scrub_text(message)[:2000]
    row.finished_at = datetime.now(timezone.utc)


def stop_queued(project_id: str, local_id: str, code: str, message: str) -> None:
    with sync_session() as session:
        rows = (
            session.query(TikTokPublish)
            .filter(
                TikTokPublish.project_id == project_id,
                TikTokPublish.id != local_id,
                TikTokPublish.status == "queued",
                TikTokPublish.publish_id.is_(None),
            )
            .all()
        )
        for row in rows:
            mark_failed(row, code, message)


def log_publish(publish_id: str | None, code: str) -> None:
    get_logger().info(
        "tiktok publish",
        extra={"stage": "tiktok", "error_code": code, "publish_id": publish_id or ""},
    )
