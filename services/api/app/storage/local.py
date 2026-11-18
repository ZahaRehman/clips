import shutil
from pathlib import Path

from app.errors import AppError
from app.storage.keys import safe_key


class LocalStorage:
    """Files under DATA_DIR. `url_for` returns the API file route for project keys."""

    def __init__(self, root: Path):
        self.root = root

    def put(self, key: str, data: bytes) -> None:
        path = self.local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self.local_path(key).read_bytes()

    def open(self, key: str):
        return self.local_path(key).open("rb")

    def delete(self, key: str) -> None:
        path = self.local_path(key)
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def url_for(self, key: str) -> str:
        rel = safe_key(key)
        if rel.startswith("projects/"):
            return "/api/v1/files/" + rel.removeprefix("projects/")
        return rel

    def local_path(self, key: str) -> Path:
        rel = safe_key(key)
        root = self.root.resolve()
        path = (root / rel).resolve()
        if path != root and root not in path.parents:
            raise AppError("FORBIDDEN", "Invalid storage key", status_code=403)
        return path
