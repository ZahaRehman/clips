from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.errors import AppError
from app.storage import get_storage

router = APIRouter(tags=["files"])


@router.get("/files/{project_id}/{file_path:path}")
async def get_project_file(project_id: str, file_path: str) -> FileResponse:
    target = get_storage().local_path(f"projects/{project_id}/{file_path}")
    if not target.is_file():
        raise AppError("NOT_FOUND", "File not found", status_code=404)
    return FileResponse(target, headers={"Cache-Control": "no-cache"})
