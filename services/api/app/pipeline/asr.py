import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Job, Project, Transcript
from app.pipeline.align import apply_aligner
from app.pipeline.job_state import update_job
from app.pipeline.paths import audio_path, rel_to_data, transcript_dir


def _model_label(model: str) -> str:
    """Single path segment. A local model directory must not become extra folders in the done-marker name."""
    return Path(model).name or "whisper"


def run_asr(session: Session, project: Project, job: Job, raw_checksum: str) -> None:
    settings = get_settings()
    update_job(session, job, stage="asr", progress=0.45)
    wav = audio_path(project.id)
    tdir = transcript_dir(project.id)
    words_file = tdir / "words.json"
    segments_file = tdir / "segments.json"
    srt_file = tdir / "episode.srt"
    aligner = (settings.asr_aligner or "none").strip().lower() or "none"
    model_label = _model_label(settings.whisper_model)
    marker = tdir / f".done_{model_label}_{aligner}_{raw_checksum[:12]}"
    legacy = tdir / f".done_{model_label}_{raw_checksum[:12]}"
    asr_done = marker.exists() or (aligner == "none" and legacy.exists())

    if words_file.exists() and segments_file.exists() and srt_file.exists() and asr_done:
        _upsert_transcript(session, project, words_file, segments_file, srt_file)
        checkpoint = dict(job.checkpoint or {})
        checkpoint["asr"] = {"skipped": True}
        update_job(session, job, progress=0.68, checkpoint=checkpoint)
        return

    model = _load_model()

    update_job(session, job, progress=0.50)
    segments_iter, info = model.transcribe(str(wav), word_timestamps=True)
    duration = max(float(getattr(info, "duration", 0) or 0), 1.0)
    words: list[dict] = []
    segments: list[dict] = []
    srt_parts: list[str] = []
    idx = 1
    next_mark = 0.05
    for seg in segments_iter:
        segments.append(
            {
                "id": seg.id,
                "start": seg.start,
                "end": seg.end,
                "text": (seg.text or "").strip(),
            }
        )
        if seg.words:
            for w in seg.words:
                words.append(
                    {
                        "word": (w.word or "").strip(),
                        "start": float(w.start),
                        "end": float(w.end),
                        "prob": float(w.probability) if w.probability is not None else None,
                    }
                )
        text = (seg.text or "").strip()
        if text:
            srt_parts.append(f"{idx}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{text}\n")
            idx += 1
        done = min(1.0, float(seg.end) / duration)
        if done >= next_mark:
            update_job(session, job, progress=0.50 + 0.18 * done)
            next_mark = done + 0.05

    if not words and not segments:
        raise AppError("ASR_FAILED", "Transcription produced no text", status_code=500)

    words = apply_aligner(words, segments, wav, info.language or "en")

    words_file.write_text(json.dumps(words, indent=2), encoding="utf-8")
    segments_file.write_text(json.dumps(segments, indent=2), encoding="utf-8")
    srt_file.write_text("\n".join(srt_parts).strip() + "\n", encoding="utf-8")
    marker.write_text("ok\n", encoding="utf-8")

    _upsert_transcript(
        session,
        project,
        words_file,
        segments_file,
        srt_file,
        language=info.language or "",
        model_name=f"faster-whisper/{_model_label(settings.whisper_model)}",
    )
    checkpoint = dict(job.checkpoint or {})
    checkpoint["asr"] = {
        "words": rel_to_data(words_file),
        "segments": rel_to_data(segments_file),
        "language": info.language,
    }
    update_job(session, job, progress=0.68, checkpoint=checkpoint)


def _upsert_transcript(
    session: Session,
    project: Project,
    words_file: Path,
    segments_file: Path,
    srt_file: Path,
    language: str = "",
    model_name: str = "",
) -> None:
    settings = get_settings()
    existing = session.query(Transcript).filter(Transcript.project_id == project.id).one_or_none()
    payload = {
        "language": language,
        "model_name": model_name or f"faster-whisper/{_model_label(settings.whisper_model)}",
        "words_path": rel_to_data(words_file),
        "segments_path": rel_to_data(segments_file),
        "srt_path": rel_to_data(srt_file),
    }
    if existing:
        for k, v in payload.items():
            if v:
                setattr(existing, k, v)
        session.add(existing)
    else:
        session.add(Transcript(project_id=project.id, **payload))
    session.commit()


_model_cache: dict[tuple[str, str, str, str], Any] = {}


def _load_model() -> Any:
    """One WhisperModel per worker process. Live and chunked jobs used to rebuild it for every window."""
    settings = get_settings()
    device, compute = _resolve_device(settings.whisper_device, settings.whisper_compute_type)
    key = (settings.whisper_model, device, compute, settings.whisper_model_dir)
    cached = _model_cache.get(key)
    if cached is not None:
        return cached
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AppError("ASR_FAILED", "faster-whisper is not installed", status_code=500) from exc
    model_dir = Path(settings.whisper_model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(settings.whisper_model, device=device, compute_type=compute, download_root=str(model_dir))
    _model_cache.clear()
    _model_cache[key] = model
    return model


def _resolve_device(device: str, compute: str) -> tuple[str, str]:
    if device == "auto":
        device = "cpu"
    if compute == "auto":
        compute = "int8" if device == "cpu" else "float16"
    return device, compute


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
