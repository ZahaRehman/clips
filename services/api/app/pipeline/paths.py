from pathlib import Path
from contextvars import ContextVar, Token

from app.config import get_settings
from app.errors import AppError
from app.storage import get_storage
from app.storage.keys import safe_key

_window_index: ContextVar[int | None] = ContextVar("cliping_window_index", default=None)


def push_window_index(index: int | None) -> Token[int | None]:
    return _window_index.set(index)


def pop_window_index(token: Token[int | None]) -> None:
    _window_index.reset(token)


def project_dir(project_id: str) -> Path:
    path = get_storage().local_path(f"projects/{project_id}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _work_dir(project_id: str) -> Path:
    index = _window_index.get()
    base = project_dir(project_id)
    if index is None:
        return base
    path = base / "windows" / f"{index:04d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def proxy_path(project_id: str) -> Path:
    path = _work_dir(project_id) / "proxy"
    path.mkdir(parents=True, exist_ok=True)
    return path / "proxy.mp4"


def audio_path(project_id: str) -> Path:
    path = _work_dir(project_id) / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path / "audio.wav"


def meta_dir(project_id: str) -> Path:
    path = _work_dir(project_id) / "meta"
    path.mkdir(parents=True, exist_ok=True)
    return path


def transcript_dir(project_id: str) -> Path:
    path = _work_dir(project_id) / "transcript"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip_dir(project_id: str, clip_id: str) -> Path:
    path = get_storage().local_path(f"projects/{project_id}/clips/{clip_id}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_project_files(project_id: str) -> None:
    get_storage().delete(_project_key(project_id))


def remove_clip_files(project_id: str, clip_id: str) -> None:
    get_storage().delete(f"{_project_key(project_id)}/clips/{safe_key(clip_id)}")


def rel_to_data(path: Path) -> str:
    data = get_settings().data_path.resolve()
    return str(path.resolve().relative_to(data))


def _project_key(project_id: str) -> str:
    key = safe_key(project_id)
    if "/" in key:
        raise AppError("FORBIDDEN", "Invalid project id", status_code=403)
    return f"projects/{key}"
