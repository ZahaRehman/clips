import asyncio
import time

import httpx
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.errors import AppError
from app.logging_setup import get_logger
from app.schemas.youtube import LiveDiscoverOut, PublishBatchOut, PublishIn, PublishOut, YouTubeStatusOut
from app.services.publish import create_publish, list_publishes
from app.youtube.discover import OFF_MESSAGE, load_discovery
from app.youtube.oauth import (
    YouTubeDisconnected,
    authorization_url,
    configured,
    connection_view,
    disconnect,
    exchange_code,
    fresh_credentials,
    web_origin,
)

router = APIRouter(tags=["youtube"])


@router.get("/youtube/status", response_model=YouTubeStatusOut)
async def youtube_status() -> dict:
    return connection_view()


@router.get("/youtube/live", response_model=LiveDiscoverOut)
async def youtube_live(
    q: str = Query(default=""),
    region: str = Query(default=""),
    category: str = Query(default=""),
) -> dict:
    key = get_settings().youtube_api_key.strip()
    if not key:
        return {"enabled": False, "cached": False, "message": OFF_MESSAGE, "streams": []}
    return await asyncio.to_thread(_load_live, key, q, region, category)


@router.get("/youtube/connect")
async def youtube_connect(next: str = Query(default="/")) -> RedirectResponse:
    return RedirectResponse(authorization_url(next))


@router.get("/youtube/callback")
async def youtube_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> RedirectResponse:
    origin = web_origin()
    if error or not code or not state:
        return RedirectResponse(f"{origin}/?youtube=denied")
    try:
        nxt = exchange_code(code, state)
    except AppError:
        return RedirectResponse(f"{origin}/?youtube=error")
    except Exception:
        get_logger().info("youtube callback failed", extra={"stage": "youtube", "error_code": "callback"})
        return RedirectResponse(f"{origin}/?youtube=error")
    return RedirectResponse(f"{origin}{nxt}")


@router.post("/youtube/disconnect", status_code=204)
async def youtube_disconnect() -> Response:
    disconnect()
    return Response(status_code=204)


@router.post("/projects/{project_id}/publish", response_model=PublishBatchOut)
async def publish_project(
    project_id: str,
    body: PublishIn,
    db: AsyncSession = Depends(get_db),
) -> PublishBatchOut:
    return await create_publish(db, project_id, body)


@router.get("/projects/{project_id}/publishes", response_model=list[PublishOut])
async def project_publishes(project_id: str, db: AsyncSession = Depends(get_db)) -> list[PublishOut]:
    return await list_publishes(db, project_id)


def _load_live(key: str, q: str, region: str, category: str) -> dict:
    with httpx.Client() as client:
        return load_discovery(key, q, region, category, _access_token(), client=client, now=time.monotonic())


def _access_token() -> str | None:
    if not configured():
        return None
    try:
        token = fresh_credentials().token
    except YouTubeDisconnected:
        return None
    except Exception:
        get_logger().info("youtube live token failed", extra={"stage": "youtube", "error_code": "YOUTUBE_DISCONNECTED"})
        return None
    return token if isinstance(token, str) and token else None
