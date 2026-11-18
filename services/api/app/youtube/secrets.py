"""Secret files under DATA_DIR/secrets. Mode 0600. Never logged and never under /files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings


def secrets_dir() -> Path:
    path = get_settings().data_path / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return secrets_dir() / "youtube_token.json"


def state_path() -> Path:
    return secrets_dir() / "youtube_oauth_state.json"


def prefs_path() -> Path:
    return secrets_dir() / "youtube_prefs.json"


def write_secret_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def read_secret_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def remove_secret(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
