from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.errors import AppError
from app.logging_setup import get_logger
from app.schemas.tiktok import TikTokCreatorOut, TikTokPublishBatchOut, TikTokPublishIn, TikTokPublishOut, TikTokStatusOut
from app.services.tiktok_publish import create_tiktok_publish, creator_view, list_tiktok_publishes
from app.tiktok.oauth import authorization_url, connection_view, disconnect, exchange_code, web_origin

router = APIRouter(tags=["tiktok"])


@router.get("/tiktok/status", response_model=TikTokStatusOut)
async def tiktok_status() -> dict:
    return connection_view()


@router.get("/tiktok/creator", response_model=TikTokCreatorOut)
async def tiktok_creator() -> TikTokCreatorOut:
    return await creator_view()


@router.get("/tiktok/connect")
async def tiktok_connect(next: str = Query(default="/")) -> RedirectResponse:
    return RedirectResponse(authorization_url(next))


@router.get("/tiktok/callback")
async def tiktok_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> RedirectResponse:
    origin = web_origin()
    if error or not code or not state:
        return RedirectResponse(f"{origin}/?tiktok=denied")
    try:
        nxt = exchange_code(code, state)
    except AppError:
        return RedirectResponse(f"{origin}/?tiktok=error")
    except Exception:
        get_logger().info("tiktok callback failed", extra={"stage": "tiktok", "error_code": "callback"})
        return RedirectResponse(f"{origin}/?tiktok=error")
    return RedirectResponse(f"{origin}{nxt}")


@router.post("/tiktok/disconnect", status_code=204)
async def tiktok_disconnect() -> Response:
    disconnect()
    return Response(status_code=204)


@router.post("/projects/{project_id}/tiktok/publish", response_model=TikTokPublishBatchOut)
async def publish_tiktok(
    project_id: str,
    body: TikTokPublishIn,
    db: AsyncSession = Depends(get_db),
) -> TikTokPublishBatchOut:
    return await create_tiktok_publish(db, project_id, body)


@router.get("/projects/{project_id}/tiktok/publishes", response_model=list[TikTokPublishOut])
async def project_tiktok_publishes(project_id: str, db: AsyncSession = Depends(get_db)) -> list[TikTokPublishOut]:
    return await list_tiktok_publishes(db, project_id)
