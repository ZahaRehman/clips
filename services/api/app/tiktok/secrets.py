"""TikTok token file under DATA_DIR/secrets. Mode 0600. Never logged."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

STATE_TTL = timedelta(minutes=10)


def secrets_dir() -> Path:
    path = get_settings().data_path / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return secrets_dir() / "tiktok_token.json"


def state_path() -> Path:
    return secrets_dir() / "tiktok_oauth_state.json"


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


def write_token(payload: dict[str, Any], *, display_name: Optional[str]) -> None:
    now = datetime.now(timezone.utc)
    expires_in = _positive_int(payload.get("expires_in"))
    refresh_expires_in = _positive_int(payload.get("refresh_expires_in"))
    access_expires_at = (now + timedelta(seconds=expires_in)).isoformat() if expires_in else None
    write_secret_json(
        token_path(),
        {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "expires_in": expires_in,
            "refresh_expires_in": refresh_expires_in,
            "access_expires_at": access_expires_at,
            "open_id": payload.get("open_id"),
            "scope": payload.get("scope"),
            "display_name": display_name,
        },
    )


def access_still_valid(data: dict[str, Any]) -> bool:
    raw = data.get("access_expires_at")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        expires = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc) + timedelta(seconds=60)


def store_state(state: str, verifier: str, next_path: str) -> None:
    now = datetime.now(timezone.utc)
    payload = _live_states(now)
    payload[state] = {"expires": (now + STATE_TTL).isoformat(), "verifier": verifier, "next": next_path}
    write_secret_json(state_path(), {"states": payload})


def pop_state(state: str) -> Optional[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    payload = _live_states(now)
    saved = payload.pop(state, None)
    write_secret_json(state_path(), {"states": payload})
    return saved if isinstance(saved, dict) else None


def _live_states(now: datetime) -> dict[str, Any]:
    raw = read_secret_json(state_path()) or {}
    states = raw.get("states") if isinstance(raw.get("states"), dict) else {}
    live: dict[str, Any] = {}
    for key, value in states.items():
        if not isinstance(value, dict):
            continue
        try:
            expires = datetime.fromisoformat(str(value.get("expires")))
        except ValueError:
            continue
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now:
            live[str(key)] = value
    return live


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
