from pathlib import Path

from app.config import get_settings
from app.errors import AppError


def watermark_token() -> str:
    """Stable hash fragment. Empty when watermarking is off."""
    text = _text()
    image = _image()
    if image:
        token = f"image:{image}"
        return f"{token}|text:{text}" if text else token
    if text:
        return f"text:{text}"
    return ""


def text_filter() -> str:
    text = _text()
    if not text:
        return ""
    font = _font_file()
    if not font:
        raise AppError(
            "EXPORT_FAILED",
            "WATERMARK_TEXT is set but DejaVuSans.ttf was not found. Set FONTS_DIR.",
            status_code=500,
        )
    font_escaped = font.replace("\\", "/").replace("\\", "\\\\").replace(":", "\\:")
    return (
        f"drawtext=fontfile={font_escaped}:text='{_escape(text)}':"
        "fontsize=36:fontcolor=white@0.85:x=48:y=48:borderw=2:bordercolor=black@0.65"
    )


def image_path() -> Path | None:
    raw = _image()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        raise AppError("EXPORT_FAILED", "WATERMARK_IMAGE is set but the file was not found", status_code=400)
    return path


def _text() -> str:
    return " ".join(get_settings().watermark_text.split())


def _image() -> str:
    return get_settings().watermark_image.strip()


def _font_file() -> str:
    settings = get_settings()
    candidates = []
    if settings.fonts_dir:
        candidates.append(Path(settings.fonts_dir) / "DejaVuSans.ttf")
    candidates.append(Path(__file__).resolve().parents[1] / "assets" / "fonts" / "DejaVuSans.ttf")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def _escape(text: str) -> str:
    out = text[:80]
    for char in ("\\", ":", ",", "'", "%"):
        out = out.replace(char, "\\" + char)
    return out
