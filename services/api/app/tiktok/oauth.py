"""TikTok Desktop Login Kit. Tokens are never logged."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.config import get_settings
from app.errors import AppError
from app.logging_setup import get_logger
from app.tiktok.errors import TikTokAPIError, message_for
from app.tiktok.secrets import (
    access_still_valid,
    pop_state,
    read_secret_json,
    remove_secret,
    store_state,
    token_path,
    write_token,
)

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
CREATOR_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
SCOPES = "user.info.basic,video.publish"
VERIFIER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


class TikTokDisconnected(Exception):
    """Refresh token missing or rejected."""


def configured() -> bool:
    settings = get_settings()
    return bool(settings.tiktok_client_key.strip() and settings.tiktok_client_secret.strip())


def web_origin() -> str:
    parsed = urlparse(get_settings().tiktok_redirect_uri)
    return f"{parsed.scheme}://{parsed.netloc}"


def safe_next(value: Optional[str]) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if value.startswith("/api"):
        return "/"
    return value.split("?", 1)[0][:500]


def pkce_verifier() -> str:
    return "".join(secrets.choice(VERIFIER_ALPHABET) for _ in range(64))


def pkce_challenge(verifier: str) -> str:
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def authorization_url(next_path: str) -> str:
    if not configured():
        raise AppError("TIKTOK_NOT_CONFIGURED", "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET", status_code=400)
    settings = get_settings()
    state = secrets.token_urlsafe(24)
    verifier = pkce_verifier()
    query = urlencode(
        {
            "client_key": settings.tiktok_client_key,
            "scope": SCOPES,
            "response_type": "code",
            "redirect_uri": settings.tiktok_redirect_uri,
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    store_state(state, verifier, safe_next(next_path))
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(code: str, state: str) -> str:
    saved = pop_state(state)
    if not saved or not saved.get("verifier"):
        raise AppError("OAUTH_STATE", "Connect expired or the state did not match. Start again.", status_code=400)
    settings = get_settings()
    payload = _token_request(
        {
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.tiktok_redirect_uri,
            "code_verifier": saved["verifier"],
        }
    )
    _require_publish_scope(payload)
    display = _display_name(str(payload.get("access_token") or ""))
    write_token(payload, display_name=display)
    get_logger().info("tiktok connected", extra={"stage": "tiktok", "error_code": "ok"})
    return str(saved.get("next") or "/")


def connection_view() -> dict[str, Any]:
    base: dict[str, Any] = {"configured": configured(), "connected": False, "display_name": None}
    if not base["configured"]:
        return base
    data = read_secret_json(token_path())
    if not data or not data.get("refresh_token"):
        return base
    base["connected"] = True
    name = data.get("display_name")
    base["display_name"] = name if isinstance(name, str) and name else None
    return base


def disconnect() -> None:
    data = read_secret_json(token_path()) or {}
    token = data.get("access_token") if isinstance(data.get("access_token"), str) else None
    if not token and isinstance(data.get("refresh_token"), str):
        token = data["refresh_token"]
    if token and configured():
        settings = get_settings()
        try:
            httpx.post(
                REVOKE_URL,
                data={
                    "client_key": settings.tiktok_client_key,
                    "client_secret": settings.tiktok_client_secret,
                    "token": token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
        except httpx.HTTPError:
            get_logger().info("tiktok revoke failed", extra={"stage": "tiktok", "error_code": "revoke"})
    remove_secret(token_path())


def fresh_access_token() -> str:
    data = read_secret_json(token_path())
    if not data or not isinstance(data.get("refresh_token"), str):
        raise TikTokDisconnected()
    if access_still_valid(data) and isinstance(data.get("access_token"), str):
        return data["access_token"]
    settings = get_settings()
    try:
        payload = _token_request(
            {
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
            }
        )
    except TikTokAPIError as exc:
        raise TikTokDisconnected() from exc
    if not payload.get("refresh_token"):
        raise TikTokDisconnected()
    display = data.get("display_name") if isinstance(data.get("display_name"), str) else None
    write_token(payload, display_name=display)
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise TikTokDisconnected()
    return token


def fetch_creator() -> dict[str, Any]:
    return api_json("POST", CREATOR_URL, {})


def api_json(method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
    token = fresh_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    try:
        response = httpx.request(method, url, headers=headers, json=body if body is not None else {}, timeout=30)
    except httpx.HTTPError as exc:
        raise TikTokAPIError("http_error", "TikTok could not be reached.", 0) from exc
    return _data(response)


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.post(
            TOKEN_URL,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise TikTokAPIError("http_error", "TikTok could not be reached.", 0) from exc
    return _data(response)


def _data(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise TikTokAPIError("http_error", "TikTok returned an unreadable response.", response.status_code) from exc
    if not isinstance(payload, dict):
        raise TikTokAPIError("http_error", "TikTok returned an unreadable response.", response.status_code)
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        if code and code != "ok":
            raise TikTokAPIError(code, message_for(code, str(error.get("message") or code)), response.status_code)
    if response.status_code >= 400:
        raise TikTokAPIError("http_error", "TikTok rejected the request.", response.status_code)
    if "access_token" in payload:
        return payload
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _display_name(access_token: str) -> Optional[str]:
    if not access_token:
        return None
    try:
        response = httpx.get(
            USER_INFO_URL,
            params={"fields": "display_name,open_id"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    user = ((payload.get("data") or {}).get("user") or {}) if isinstance(payload, dict) else {}
    name = user.get("display_name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _require_publish_scope(payload: dict[str, Any]) -> None:
    scope = str(payload.get("scope") or "")
    granted = {part.strip() for part in scope.replace(" ", ",").split(",") if part.strip()}
    if "video.publish" not in granted:
        raise AppError(
            "OAUTH_SCOPE",
            "TikTok did not grant video.publish. Connect again and approve posting.",
            status_code=400,
        )

