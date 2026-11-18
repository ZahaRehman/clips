import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.errors import error_body
from app.logging_setup import get_logger

_PUBLIC_API = {"/api/v1/health"}


def install_auth(app: FastAPI) -> None:
    @app.middleware("http")
    async def api_token_middleware(request: Request, call_next):
        settings = get_settings()
        token = settings.api_token.strip()
        if not token or _is_public(request.url.path):
            return await call_next(request)
        provided = _provided_token(request)
        if provided and secrets.compare_digest(provided, token):
            return await call_next(request)
        get_logger().info("unauthorized", extra={"stage": "auth"})
        return JSONResponse(status_code=401, content=error_body("UNAUTHORIZED", "Missing or invalid API token"))


def _is_public(path: str) -> bool:
    trimmed = path.rstrip("/") or "/"
    return trimmed in _PUBLIC_API


def _provided_token(request: Request) -> str:
    header = (request.headers.get("x-api-token") or "").strip()
    if header:
        return header
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""
