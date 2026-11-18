"""YouTube OAuth: consent URL, token file, refresh, disconnect. Tokens are never logged."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.errors import AppError
from app.logging_setup import get_logger
from app.youtube.secrets import prefs_path, read_secret_json, remove_secret, state_path, token_path, write_secret_json

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
SCOPES = [UPLOAD_SCOPE, READONLY_SCOPE]
STATE_TTL = timedelta(minutes=10)
UNAVAILABLE = "Connected (channel name unavailable)"


class YouTubeDisconnected(Exception):
    """Refresh token missing or rejected. Already posted videos stay on YouTube."""


def configured() -> bool:
    settings = get_settings()
    return bool(settings.youtube_client_id.strip() and settings.youtube_client_secret.strip())


def web_origin() -> str:
    parsed = urlparse(get_settings().youtube_redirect_uri)
    return f"{parsed.scheme}://{parsed.netloc}"


def safe_next(value: Optional[str]) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if value.startswith("/api"):
        return "/"
    return value.split("?", 1)[0][:500]


def authorization_url(next_path: str) -> str:
    if not configured():
        raise AppError("YOUTUBE_NOT_CONFIGURED", "Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET", status_code=400)
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = get_settings().youtube_redirect_uri
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    _store_state(state, getattr(flow, "code_verifier", None), safe_next(next_path))
    return url


def exchange_code(code: str, state: str) -> str:
    saved = _pop_state(state)
    if not saved:
        raise AppError("OAUTH_STATE", "Connect expired or the state did not match. Start again.", status_code=400)
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, state=state)
    flow.redirect_uri = get_settings().youtube_redirect_uri
    verifier = saved.get("verifier")
    if verifier:
        flow.code_verifier = verifier
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise AppError(
            "OAUTH_NO_REFRESH",
            "Google did not return a refresh token. Remove Cliping's access in your Google account and connect again.",
            status_code=400,
        )
    title, channel_status = fetch_channel_title(creds)
    _write_token(creds, channel_title=title, channel_status=channel_status)
    get_logger().info("youtube connected", extra={"stage": "youtube", "error_code": channel_status})
    return str(saved.get("next") or "/")


def connection_view() -> dict[str, Any]:
    settings = get_settings()
    base = {
        "configured": configured(),
        "connected": False,
        "channel_title": None,
        "channel_label": None,
        "made_for_kids": _made_for_kids(),
        "default_spacing_min": settings.youtube_default_spacing_min,
    }
    if not base["configured"]:
        return base
    data = read_secret_json(token_path())
    if not data or not data.get("refresh_token"):
        return base
    title = data.get("channel_title") if isinstance(data.get("channel_title"), str) else None
    base["connected"] = True
    base["channel_title"] = title or None
    base["channel_label"] = title if data.get("channel_status") == "ok" and title else UNAVAILABLE
    return base


def disconnect() -> None:
    data = read_secret_json(token_path()) or {}
    refresh = data.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        try:
            httpx.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": refresh},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
        except httpx.HTTPError:
            get_logger().info("youtube revoke failed", extra={"stage": "youtube", "error_code": "revoke"})
    remove_secret(token_path())


def fresh_credentials():
    """Access token for the worker. Refreshes from the stored refresh token."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    data = read_secret_json(token_path())
    if not data or not isinstance(data.get("refresh_token"), str):
        raise YouTubeDisconnected()
    settings = get_settings()
    creds = Credentials(
        token=data.get("token") if isinstance(data.get("token"), str) else None,
        refresh_token=data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.youtube_client_id,
        client_secret=settings.youtube_client_secret,
        scopes=list(data.get("scopes") or SCOPES),
    )
    expiry = _naive_expiry(data.get("expiry"))
    if expiry is not None:
        creds.expiry = expiry
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise YouTubeDisconnected() from exc
        _write_token(
            creds,
            channel_title=data.get("channel_title") if isinstance(data.get("channel_title"), str) else None,
            channel_status=str(data.get("channel_status") or "unavailable"),
        )
    return creds


def youtube_client(creds):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def fetch_channel_title(creds) -> tuple[Optional[str], str]:
    """channels.list under the upload scope. A 403 keeps the upload scope and hides the name."""
    from googleapiclient.errors import HttpError

    try:
        response = youtube_client(creds).channels().list(part="snippet", mine=True).execute()
    except HttpError as exc:
        status = int(getattr(exc.resp, "status", 0) or 0)
        if status == 403:
            return None, "unavailable"
        raise
    items = response.get("items") or []
    if not items:
        return None, "unavailable"
    title = ((items[0].get("snippet") or {}).get("title") or "").strip()
    if not title:
        return None, "unavailable"
    return title, "ok"


def remember_made_for_kids(value: bool) -> None:
    prefs = read_secret_json(prefs_path()) or {}
    prefs["made_for_kids"] = bool(value)
    write_secret_json(prefs_path(), prefs)


def cached_category_check(category_id: str) -> Optional[str]:
    prefs = read_secret_json(prefs_path()) or {}
    if prefs.get("category_id") == category_id and prefs.get("category_check") in {"ok", "unverified"}:
        return str(prefs["category_check"])
    return None


def remember_category_check(category_id: str, result: str) -> None:
    prefs = read_secret_json(prefs_path()) or {}
    prefs["category_id"] = category_id
    prefs["category_check"] = result
    write_secret_json(prefs_path(), prefs)


def _made_for_kids() -> Optional[bool]:
    prefs = read_secret_json(prefs_path()) or {}
    value = prefs.get("made_for_kids")
    return value if isinstance(value, bool) else None


def _client_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "web": {
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.youtube_redirect_uri],
        }
    }


def _write_token(creds, *, channel_title: Optional[str], channel_status: str) -> None:
    expiry = creds.expiry.isoformat() if getattr(creds, "expiry", None) else None
    write_secret_json(
        token_path(),
        {
            "refresh_token": creds.refresh_token,
            "token": creds.token,
            "expiry": expiry,
            "scopes": list(creds.scopes or SCOPES),
            "channel_title": channel_title,
            "channel_status": channel_status,
        },
    )


def _store_state(state: str, verifier: Optional[str], next_path: str) -> None:
    now = datetime.now(timezone.utc)
    payload = _live_states(now)
    payload[state] = {
        "expires": (now + STATE_TTL).isoformat(),
        "verifier": verifier,
        "next": next_path,
    }
    write_secret_json(state_path(), {"states": payload})


def _pop_state(state: str) -> Optional[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    payload = _live_states(now)
    saved = payload.pop(state, None)
    write_secret_json(state_path(), {"states": payload})
    if not isinstance(saved, dict):
        return None
    return saved


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


def _naive_expiry(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

