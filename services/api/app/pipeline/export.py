import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Clip, ClipExport, Job, Project
from app.pipeline.captions import caption_payload, resolve_caption_words, write_ass, write_srt
from app.pipeline.faces import analysis_video, detect_face_center
from app.pipeline.ffmpeg_util import ffprobe, has_audio, media_duration, run_cmd, video_size, x264_args
from app.pipeline.ingest import find_raw
from app.pipeline.job_state import set_project_status, update_job
from app.pipeline.paths import clip_dir, rel_to_data
from app.pipeline.reframe import box_matches_trim, face_crop, ffmpeg_crop_filter, output_size, static_crop
from app.pipeline.watermark import image_path, text_filter, watermark_token

LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
PRESET = "shorts_1080"


def resolve_export_source(project_id: str, clip: Clip | None = None) -> Path:
    window = _window_media(clip) if clip is not None else None
    if window is not None:
        return window
    try:
        return find_raw(project_id)
    except AppError:
        from app.pipeline.paths import proxy_path

        proxy = proxy_path(project_id)
        if proxy.is_file():
            return proxy
        raise AppError(
            "EXPORT_FAILED",
            "No source video for this project. Upload the file again.",
            status_code=400,
        )


def export_input_hash(
    *,
    t_start: float,
    t_end: float,
    crop: dict,
    words: list[dict],
    preset: str,
    width: int,
    height: int,
    watermark: str = "",
) -> str:
    payload = {
        "t_start": round(t_start, 3),
        "t_end": round(t_end, 3),
        "crop": {
            "mode": crop.get("mode"),
            "x": crop.get("x"),
            "y": crop.get("y"),
            "w": crop.get("w"),
            "h": crop.get("h"),
        },
        "words": caption_payload(words),
        "preset": preset,
        "width": width,
        "height": height,
        "loudnorm": LOUDNORM,
    }
    if watermark:
        payload["watermark"] = watermark
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def export_size(mode: str | None) -> tuple[int, int]:
    settings = get_settings()
    return output_size(
        mode,
        (settings.export_width, settings.export_height),
        (settings.full_export_width, settings.full_export_height),
    )


def should_skip_encode(force: bool, file_exists: bool, stored_hash: str, digest: str) -> bool:
    return (not force) and file_exists and bool(stored_hash) and stored_hash == digest


def planned_export_hash(clip: Clip) -> str | None:
    """Hash of the export inputs when no face detection is required.

    Returns None when face mode still needs a fresh detection, so the caller
    enqueues a worker instead of guessing.
    """
    settings = get_settings()
    t_start, t_end = _trim(clip)
    stored = dict(clip.crop or {})
    mode = str(stored.get("mode") or "face")
    source = resolve_export_source(clip.project_id, clip)
    src_w, src_h = video_size(ffprobe(source))
    if src_w <= 0 or src_h <= 0:
        raise AppError("EXPORT_FAILED", "Could not read source video size", status_code=500)
    if mode == "face" and not box_matches_trim(stored, t_start, t_end):
        return None
    out_w, out_h = export_size(mode)
    crop = stored if mode == "face" else static_crop(mode, stored, src_w, src_h, out_w, out_h, t_start, t_end)
    words = _caption_words(clip, t_start, t_end)
    return export_input_hash(
        t_start=t_start,
        t_end=t_end,
        crop=crop,
        words=words,
        preset=PRESET,
        width=out_w,
        height=out_h,
        watermark=watermark_token(),
    )


def run_export_clip(session: Session, clip_id: str, export_id: str, job_id: str) -> None:
    clip = session.get(Clip, clip_id)
    export = session.get(ClipExport, export_id)
    job = session.get(Job, job_id)
    if not clip or not export or not job:
        raise AppError("EXPORT_FAILED", "Clip/export/job not found", status_code=404)

    project = session.get(Project, clip.project_id)
    if not project:
        raise AppError("EXPORT_FAILED", "Project not found", status_code=404)

    update_job(session, job, stage="export", status="running", progress=0.1)
    render_export(
        session,
        clip,
        export,
        force=bool((job.checkpoint or {}).get("force")),
        on_encode=lambda: update_job(session, job, stage="export", progress=0.4),
    )
    update_job(session, job, stage="export", status="succeeded", progress=1.0)
    if project.status != "live":
        set_project_status(session, project, "done")


def render_export(
    session: Session,
    clip: Clip,
    export: ClipExport,
    *,
    force: bool,
    on_encode: Callable[[], None] | None = None,
) -> None:
    """Render one clip into `export`. Owns the export row and clip status, not the job or project."""
    settings = get_settings()
    export.status = "rendering"
    export.error = None
    session.add(export)
    session.commit()

    t_start, t_end = _trim(clip)
    duration = max(0.1, t_end - t_start)
    source = resolve_export_source(clip.project_id, clip)
    probe = ffprobe(source)
    src_w, src_h = video_size(probe)
    if src_w <= 0 or src_h <= 0:
        raise AppError("EXPORT_FAILED", "Could not read source video size", status_code=500)

    out_w, out_h = export_size((clip.crop or {}).get("mode") or "face")
    crop = _resolve_crop(clip, clip.project_id, source, src_w, src_h, out_w, out_h, t_start, t_end, force)
    clip.crop = crop

    selected = _caption_words(clip, t_start, t_end)
    cdir = clip_dir(clip.project_id, clip.id)
    srt_path = cdir / "captions.srt"
    ass_path = cdir / "captions.ass"
    write_srt(srt_path, selected, t_start)
    write_ass(ass_path, selected, t_start, out_w, out_h)

    digest = export_input_hash(
        t_start=t_start,
        t_end=t_end,
        crop=crop,
        words=selected,
        preset=export.preset or PRESET,
        width=out_w,
        height=out_h,
        watermark=watermark_token(),
    )
    out_mp4 = cdir / "export.mp4"
    hash_path = cdir / "export.hash"
    stored_hash = hash_path.read_text(encoding="utf-8").strip() if hash_path.is_file() else ""
    if should_skip_encode(force, out_mp4.is_file(), stored_hash, digest):
        _finish(session, clip, export, out_mp4, digest)
        return

    fonts_dir = settings.fonts_dir or str(Path(__file__).resolve().parents[1] / "assets" / "fonts")
    mark = image_path()
    draw = text_filter()
    if on_encode:
        on_encode()
    try:
        _encode_short(
            settings.ffmpeg_path,
            source,
            probe,
            src_w,
            src_h,
            out_w,
            out_h,
            crop,
            ass_path,
            fonts_dir,
            t_start,
            duration,
            mark,
            draw,
            out_mp4,
        )
    except AppError:
        proxy = _analysis_video(clip, source)
        if not proxy.is_file() or proxy.resolve() == Path(source).resolve():
            raise
        proxy_probe = ffprobe(proxy)
        proxy_w, proxy_h = video_size(proxy_probe)
        if proxy_w <= 0 or proxy_h <= 0:
            raise
        _encode_short(
            settings.ffmpeg_path,
            proxy,
            proxy_probe,
            proxy_w,
            proxy_h,
            out_w,
            out_h,
            crop,
            ass_path,
            fonts_dir,
            t_start,
            duration,
            mark,
            draw,
            out_mp4,
        )
    hash_path.write_text(digest + "\n", encoding="utf-8")
    _finish(session, clip, export, out_mp4, digest)


def _encode_short(
    ffmpeg: str,
    source: Path,
    probe: dict,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    crop: dict,
    ass_path: Path,
    fonts_dir: str,
    t_start: float,
    duration: float,
    mark: Path | None,
    draw: str,
    out_mp4: Path,
) -> None:
    """Encode one short.

    `-ss` sits before `-i`: ffmpeg jumps to the keyframe before t_start and decodes from
    there, frame-exact, instead of decoding the whole file from 0 (measured 135 s -> 81 s
    on a 35 s short 240 s into a 1080p60 chunk; same first frames, timestamps start at 0
    so the ASS times stay relative to t_start).
    """
    settings = get_settings()
    base_vf = f"{ffmpeg_crop_filter(crop, src_w, src_h, out_w, out_h)},{_ass_filter(ass_path, fonts_dir)}"
    cmd = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-fflags",
        "+discardcorrupt",
        "-err_detect",
        "ignore_err",
        "-ss",
        f"{t_start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
    ]
    if mark is not None:
        cmd.extend(["-i", str(mark)])
    if mark is not None:
        graph = f"[0:v]{base_vf}[base];[1:v]scale=180:-1[wm];[base][wm]overlay=W-w-48:48"
        if draw:
            graph += f",{draw}"
        graph += "[vout]"
        cmd.extend(["-filter_complex", graph, "-map", "[vout]"])
        if has_audio(probe):
            cmd.extend(["-map", "0:a:0", "-af", LOUDNORM, "-c:a", "aac", "-b:a", settings.export_audio_bitrate])
        else:
            cmd.append("-an")
    else:
        vf = f"{base_vf},{draw}" if draw else base_vf
        cmd.extend(["-vf", vf])
        if has_audio(probe):
            cmd.extend(["-af", LOUDNORM, "-c:a", "aac", "-b:a", settings.export_audio_bitrate])
        else:
            cmd.append("-an")
    cmd.extend(x264_args(settings.export_preset, settings.export_max_fps, crf=settings.export_crf))
    cmd.extend(["-movflags", "+faststart", str(out_mp4)])
    run_cmd(cmd)


def _resolve_crop(
    clip: Clip,
    project_id: str,
    source: Path,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    t_start: float,
    t_end: float,
    force: bool,
) -> dict:
    stored = dict(clip.crop or {})
    mode = str(stored.get("mode") or "face")
    if mode == "face":
        if not force and box_matches_trim(stored, t_start, t_end):
            return stored
        center = detect_face_center(_analysis_video(clip, source), t_start, t_end)
        return face_crop(center, src_w, src_h, out_w, out_h, t_start, t_end)
    return static_crop(mode, stored, src_w, src_h, out_w, out_h, t_start, t_end)


def _caption_words(clip: Clip, t_start: float, t_end: float) -> list[dict]:
    words_path = _words_file(clip)
    if not words_path.is_file():
        raise AppError("EXPORT_FAILED", "Transcript missing", status_code=400)
    words = json.loads(words_path.read_text(encoding="utf-8"))
    selected = resolve_caption_words(words, t_start, t_end, clip.caption_text_override)
    if selected:
        return selected
    return [{"word": clip.title or "…", "start": t_start, "end": min(t_end, t_start + 2)}]


def _window_media(clip: Clip) -> Path | None:
    row = _window_row(clip)
    if row is None:
        return None
    settings = get_settings()
    path = settings.data_path / row.path
    if path.is_file():
        return path
    proxy = (
        settings.data_path
        / "projects"
        / clip.project_id
        / "windows"
        / f"{int(row.index):04d}"
        / "proxy"
        / "proxy.mp4"
    )
    return proxy if proxy.is_file() else None


def _words_file(clip: Clip) -> Path:
    settings = get_settings()
    row = _window_row(clip)
    if row is not None:
        return (
            settings.data_path
            / "projects"
            / clip.project_id
            / "windows"
            / f"{int(row.index):04d}"
            / "transcript"
            / "words.json"
        )
    return settings.data_path / "projects" / clip.project_id / "transcript" / "words.json"


def _analysis_video(clip: Clip, source: Path) -> Path:
    row = _window_row(clip)
    if row is not None:
        proxy = (
            get_settings().data_path
            / "projects"
            / clip.project_id
            / "windows"
            / f"{int(row.index):04d}"
            / "proxy"
            / "proxy.mp4"
        )
        if proxy.is_file():
            return proxy
    return analysis_video(clip.project_id, source)


def _window_row(clip: Clip):
    if clip.stream_offset_sec is None:
        return None
    from app.db.sync import sync_session
    from app.models import LiveWindow

    target = round(float(clip.stream_offset_sec), 3)
    with sync_session() as session:
        rows = session.query(LiveWindow).filter(LiveWindow.project_id == clip.project_id).all()
        for row in rows:
            if round(float(row.stream_start_sec), 3) == target:
                session.expunge(row)
                return row
    return None


def _trim(clip: Clip) -> tuple[float, float]:
    t_start = float(clip.user_t_start if clip.user_t_start is not None else clip.t_start)
    t_end = float(clip.user_t_end if clip.user_t_end is not None else clip.t_end)
    return t_start, t_end


def _ass_filter(ass_path: Path, fonts_dir: str) -> str:
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    fonts_escaped = fonts_dir.replace("\\", "/").replace(":", "\\:")
    return f"ass={ass_escaped}:fontsdir={fonts_escaped}"


def _finish(session: Session, clip: Clip, export: ClipExport, out_mp4: Path, digest: str) -> None:
    out_probe = ffprobe(out_mp4)
    width, height = video_size(out_probe)
    export.path = rel_to_data(out_mp4)
    export.status = "ready"
    export.width = width
    export.height = height
    export.duration_sec = media_duration(out_probe)
    export.input_hash = digest
    clip.status = "exported"
    session.add(export)
    session.add(clip)
    session.commit()
