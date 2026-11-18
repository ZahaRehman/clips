from app.errors import AppError


def safe_key(key: str) -> str:
    """Relative storage key. Rejects absolute paths and `..`."""
    normalized = key.replace("\\", "/").strip().lstrip("/")
    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise AppError("FORBIDDEN", "Invalid storage key", status_code=403)
        parts.append(part)
    if not parts:
        raise AppError("FORBIDDEN", "Invalid storage key", status_code=403)
    return "/".join(parts)
