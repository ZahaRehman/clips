from pathlib import Path
from typing import Protocol, runtime_checkable

from app.config import get_settings
from app.errors import AppError
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage

_storage: LocalStorage | S3Storage | None = None


@runtime_checkable
class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def open(self, key: str): ...

    def delete(self, key: str) -> None: ...

    def url_for(self, key: str) -> str: ...

    def local_path(self, key: str) -> Path: ...


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        settings = get_settings()
        backend = settings.storage_backend.strip().lower()
        if backend == "local":
            _storage = LocalStorage(Path(settings.data_dir))
        elif backend == "s3":
            _storage = S3Storage(
                bucket=settings.s3_bucket,
                region=settings.s3_region,
                prefix=settings.s3_prefix,
                endpoint_url=settings.s3_endpoint_url,
            )
        else:
            raise AppError("CONFIG", f"Unknown STORAGE_BACKEND {settings.storage_backend}", status_code=500)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
