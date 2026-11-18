from contextlib import asynccontextmanager
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import install_auth
from app.api.routes import admin, clips, dev, files, health, live, projects, tiktok, youtube
from app.config import get_settings
from app.errors import AppError, app_error_handler, http_error_handler
from app.logging_setup import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    settings = get_settings()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    from app.live.control import resume_orphaned

    await asyncio.to_thread(resume_orphaned)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Cliping API", version="0.1.0", lifespan=lifespan)
    install_auth(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(dev.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(live.router, prefix="/api/v1")
    app.include_router(clips.router, prefix="/api/v1")
    app.include_router(youtube.router, prefix="/api/v1")
    app.include_router(tiktok.router, prefix="/api/v1")
    app.include_router(files.router, prefix="/api/v1")
    return app


app = create_app()
