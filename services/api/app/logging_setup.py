import json
import logging

from app.redact import scrub_text

_FIELDS = (
    "project_id",
    "job_id",
    "stage",
    "duration_ms",
    "bytes_reclaimed",
    "model",
    "http_status",
    "quota_id",
    "publish_id",
    "video_id",
    "error_code",
)
_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_text(record.getMessage()),
        }
        for key in _FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("cliping")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _CONFIGURED = True


def get_logger() -> logging.Logger:
    configure_logging()
    return logging.getLogger("cliping")
