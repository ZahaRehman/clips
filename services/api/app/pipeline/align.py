"""Optional word-time alignment after faster-whisper.

`ASR_ALIGNER=none` (default) returns the faster-whisper words unchanged and does
not import whisperx. `whisperx` is an extra install; it is not in the default
image. See the README for the extra packages and RAM.
"""

from pathlib import Path

from app.config import get_settings
from app.errors import AppError


def apply_aligner(words: list[dict], segments: list[dict], wav: Path, language: str) -> list[dict]:
    aligner = (get_settings().asr_aligner or "none").strip().lower()
    if aligner in {"", "none"}:
        return words
    if aligner != "whisperx":
        raise AppError("ASR_FAILED", "ASR_ALIGNER must be none or whisperx", status_code=500)
    return _align_whisperx(segments, wav, language)


def _align_whisperx(segments: list[dict], wav: Path, language: str) -> list[dict]:
    try:
        import whisperx
    except ImportError as exc:
        raise AppError(
            "ASR_FAILED",
            "ASR_ALIGNER=whisperx but whisperx is not installed. "
            "Leave ASR_ALIGNER=none, or install the extra deps in the README.",
            status_code=500,
        ) from exc

    settings = get_settings()
    device = "cuda" if settings.whisper_device == "cuda" else "cpu"
    audio = whisperx.load_audio(str(wav))
    model, metadata = whisperx.load_align_model(language_code=language or "en", device=device)
    aligned = whisperx.align(
        [{"start": row["start"], "end": row["end"], "text": row.get("text") or ""} for row in segments],
        model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    tighter: list[dict] = []
    for seg in aligned.get("segments") or []:
        for word in seg.get("words") or []:
            if "start" not in word or "end" not in word:
                continue
            token = (word.get("word") or "").strip()
            if not token:
                continue
            tighter.append(
                {
                    "word": token,
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "prob": float(word["score"]) if word.get("score") is not None else None,
                }
            )
    if not tighter:
        raise AppError("ASR_FAILED", "WhisperX alignment produced no words", status_code=500)
    return tighter
