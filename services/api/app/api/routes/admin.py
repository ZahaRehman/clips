import asyncio

from fastapi import APIRouter

from app.pipeline.cleanup import run_cleanup

router = APIRouter(tags=["admin"])


@router.post("/admin/cleanup")
async def admin_cleanup() -> dict[str, int]:
    return await asyncio.to_thread(run_cleanup)
