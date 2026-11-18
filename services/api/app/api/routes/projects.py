import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.config import get_settings
from app.db.session import SessionLocal, get_db
from app.errors import AppError
from app.models import Asset, Clip, ClipExport, Job, LiveWindow, Project, Transcript, YouTubePublish
from app.pipeline.clip_plan import clip_bounds
from app.pipeline.ffmpeg_util import ffprobe, media_duration
from app.pipeline.framing import crop_for_framing, framing_of, parse_framing
from app.pipeline.ranges import plan_ranges
from app.pipeline.disk import ensure_free_disk
from app.pipeline.paths import raw_dir, remove_project_files
from app.pipeline.runner import AUTOPILOT_STAGES, STAGES
from app.schemas.project import (
    ChunkStatus,
    ClipCreate,
    ClipOut,
    JobOut,
    FramingUpdate,
    ProjectCreateUrl,
    ProjectCreated,
    ProjectOut,
    ReprocessBody,
)
from app.services.autopilot import autopilot_summary
from app.services.clip_view import clip_outs
from app.services.enqueue import enqueue
from app.services.exports import queue_clip_export
from app.services.live_view import live_payload
from app.vod.schedule import FETCH_QUEUE, chunk_counts

router = APIRouter(tags=["projects"])

ALLOWED_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_TERMINAL_PROJECT = {"ready_for_review", "failed", "done"}
_TERMINAL_JOB = {"succeeded", "failed", "cancelled"}


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_db)) -> list[ProjectOut]:
    result = await db.execute(select(Project).order_by(Project.created_at.desc()).limit(50))
    projects = result.scalars().all()
    return [await _project_out(db, p) for p in projects]


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)) -> ProjectOut:
    project = await _require_project(db, project_id)
    return await _project_out(db, project)


@router.post("/projects", response_model=ProjectCreated, status_code=202)
async def create_project(request: Request, db: AsyncSession = Depends(get_db)) -> ProjectCreated:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await _create_from_url(request, db)
    return await _create_from_upload(request, db)


@router.get("/projects/{project_id}/events")
async def project_events(project_id: str) -> StreamingResponse:
    async def stream():
        last = ""
        for _ in range(900):
            async with SessionLocal() as db:
                project = await db.get(Project, project_id)
                if not project:
                    yield _sse({"error": "Project not found"})
                    return
                job = await _latest_job(db, project.id)
                payload = {
                    "status": project.status,
                    "stage": job.stage if job else "",
                    "progress": job.progress if job else 0,
                    "job_status": job.status if job else "",
                    "error": job.error if job else None,
                }
            line = json.dumps(payload)
            status = payload["status"]
            job_status = payload["job_status"]
            if line != last:
                yield _sse(payload)
                last = line
            if status in _TERMINAL_PROJECT and job_status in _TERMINAL_JOB and status != "exporting":
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    project = await _require_project(db, project_id)
    if project.source_type == "live" and project.status == "live":
        from app.live.control import request_stop

        await db.commit()
        await asyncio.to_thread(request_stop, project.id)
    clip_ids = select(Clip.id).where(Clip.project_id == project.id)
    await db.execute(delete(YouTubePublish).where(YouTubePublish.project_id == project.id))
    await db.execute(delete(ClipExport).where(ClipExport.clip_id.in_(clip_ids)))
    await db.execute(delete(Clip).where(Clip.project_id == project.id))
    await db.execute(delete(LiveWindow).where(LiveWindow.project_id == project.id))
    await db.execute(delete(Job).where(Job.project_id == project.id))
    await db.execute(delete(Asset).where(Asset.project_id == project.id))
    await db.execute(delete(Transcript).where(Transcript.project_id == project.id))
    await db.delete(project)
    await db.commit()
    remove_project_files(project_id)
    return Response(status_code=204)


@router.post("/projects/{project_id}/reprocess", response_model=ProjectCreated, status_code=202)
async def reprocess_project(
    project_id: str,
    body: ReprocessBody,
    db: AsyncSession = Depends(get_db),
) -> ProjectCreated:
    project = await _require_project(db, project_id)
    if body.from_stage in {"captions", "export"}:
        return await _reexport_accepted(db, project)
    stages = STAGES if project.autopilot else tuple(s for s in STAGES if s not in AUTOPILOT_STAGES)
    allowed = (*stages, "captions", "export")
    if body.from_stage not in stages:
        raise AppError("INVALID_STAGE", f"from_stage must be one of: {', '.join(allowed)}", status_code=400)
    return await _enqueue_pipeline(db, project, body.from_stage)


@router.post("/projects/{project_id}/propose", response_model=ProjectCreated, status_code=202)
async def propose_project(project_id: str, db: AsyncSession = Depends(get_db)) -> ProjectCreated:
    project = await _require_project(db, project_id)
    return await _enqueue_pipeline(db, project, "segment_score")


@router.post("/projects/{project_id}/post-metadata", response_model=ProjectCreated, status_code=202)
async def rerun_post_metadata(project_id: str, db: AsyncSession = Depends(get_db)) -> ProjectCreated:
    """Redraft post text for clips the user has not edited (one Gemini call)."""
    project = await _require_project(db, project_id)
    if project.status in {"ingesting", "processing", "created"}:
        raise AppError("BUSY", "Wait for the pipeline to finish before redrafting post text", status_code=409)
    job = Job(project_id=project.id, kind="metadata", status="queued", stage="queued", progress=0.0)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    try:
        await enqueue("run_metadata", project.id, job.id)
    except Exception as exc:
        job.status = "failed"
        job.error = "Could not queue the metadata job."
        await db.commit()
        raise AppError(
            "QUEUE_FAILED",
            "Could not queue the metadata job. Check that Redis and the worker are running.",
            status_code=503,
        ) from exc
    return ProjectCreated(id=project.id, status=project.status)


@router.get("/projects/{project_id}/transcript")
async def get_transcript(project_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    await _require_project(db, project_id)
    result = await db.execute(select(Transcript).where(Transcript.project_id == project_id))
    transcript = result.scalar_one_or_none()
    if not transcript:
        raise AppError("NOT_READY", "Transcript not ready", status_code=404)

    words = json.loads((settings.data_path / transcript.words_path).read_text(encoding="utf-8"))
    segments = json.loads((settings.data_path / transcript.segments_path).read_text(encoding="utf-8"))
    return {
        "language": transcript.language,
        "model_name": transcript.model_name,
        "words": words,
        "segments": segments,
        "proxy_url": f"/api/v1/files/{project_id}/proxy/proxy.mp4",
    }


@router.get("/projects/{project_id}/clips", response_model=list[ClipOut])
async def list_clips(project_id: str, db: AsyncSession = Depends(get_db)) -> list[ClipOut]:
    await _require_project(db, project_id)
    result = await db.execute(
        select(Clip).where(Clip.project_id == project_id).order_by(Clip.rank.asc(), Clip.score.desc(), Clip.t_start)
    )
    return await clip_outs(db, project_id, list(result.scalars().all()))


@router.post("/projects/{project_id}/clips", response_model=ClipOut)
async def create_clip(project_id: str, body: ClipCreate, db: AsyncSession = Depends(get_db)) -> ClipOut:
    project = await _require_project(db, project_id)
    if body.t_end <= body.t_start:
        raise AppError("INVALID_CLIP", "t_end must be greater than t_start", status_code=400)
    duration = body.t_end - body.t_start
    min_sec, max_sec = clip_bounds(project)
    if duration < min_sec or duration > max_sec:
        raise AppError(
            "INVALID_CLIP",
            f"Clip duration must be between {min_sec:g} and {max_sec:g}s",
            status_code=400,
        )
    media_dur = float((project.source_meta or {}).get("duration") or 0)
    if media_dur and body.t_end > media_dur + 0.5:
        raise AppError("INVALID_CLIP", "t_end beyond media duration", status_code=400)

    clip = Clip(
        project_id=project_id,
        t_start=body.t_start,
        t_end=body.t_end,
        user_t_start=body.t_start,
        user_t_end=body.t_end,
        title=body.title or f"Clip {body.t_start:.1f}-{body.t_end:.1f}",
        status="accepted",
        crop=crop_for_framing(framing_of(project.source_meta)),
    )
    db.add(clip)
    await db.commit()
    await db.refresh(clip)
    return ClipOut.model_validate(clip)


async def _create_from_upload(request: Request, db: AsyncSession) -> ProjectCreated:
    settings = get_settings()
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise AppError("INVALID_FILE", "Upload a video file, or send JSON with url and disclaimer_accepted", status_code=400)
    filename = upload.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise AppError("INVALID_FILE", f"Unsupported extension {ext}", status_code=400)

    title_raw = form.get("title")
    title = title_raw if isinstance(title_raw, str) and title_raw.strip() else Path(filename).stem
    framing_raw = form.get("framing")
    framing = parse_framing(framing_raw if isinstance(framing_raw, str) else None)
    autopilot = str(form.get("autopilot") or "").strip().lower() in {"1", "true", "on", "yes"}
    count_raw = form.get("clip_count")
    clip_count = _form_int(count_raw) if isinstance(count_raw, str) and count_raw.strip() else None
    declared = request.headers.get("content-length") or ""
    extra = int(declared) if declared.isdigit() else 0
    ensure_free_disk(extra)
    project = _new_project(
        title=title,
        source_type="upload",
        source_meta={"original_filename": filename, "framing": framing},
        autopilot=autopilot,
        clip_count=clip_count,
    )
    dest = raw_dir(project.id) / f"source{ext}"
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_source_bytes:
                dest.unlink(missing_ok=True)
                raise AppError("FILE_TOO_LARGE", "Upload exceeds max size", status_code=400)
            out.write(chunk)
    meta = dict(project.source_meta or {})
    meta["bytes"] = size
    project.source_meta = meta
    probed = await asyncio.to_thread(_safe_duration, dest)
    if probed > settings.max_source_duration_sec:
        dest.unlink(missing_ok=True)
        raise AppError(
            "INGEST_FAILED",
            f"Source longer than {settings.max_source_duration_sec:g}s",
            status_code=400,
        )
    if _needs_chunks(probed):
        _apply_chunks(project, probed)
        return await _enqueue_first_chunk(db, project)
    return await _enqueue_pipeline(db, project, "ingest")


async def _create_from_url(request: Request, db: AsyncSession) -> ProjectCreated:
    settings = get_settings()
    if not settings.allow_url_ingest:
        raise AppError(
            "URL_INGEST_DISABLED",
            "URL ingest is disabled. Set ALLOW_URL_INGEST=true or upload a file you have rights to.",
            status_code=403,
        )
    try:
        payload = ProjectCreateUrl.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError) as exc:
        raise AppError("INVALID_REQUEST", "JSON body needs url and disclaimer_accepted", status_code=400) from exc
    if payload.disclaimer_accepted is not True:
        raise AppError("DISCLAIMER_REQUIRED", "Accept the rights disclaimer before submitting a URL", status_code=400)
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise AppError("INVALID_URL", "URL must start with http:// or https://", status_code=400)
    from app.live.probe import peek_url

    info = await asyncio.to_thread(peek_url, url)
    if info and str(info.get("live_status") or "") == "is_live":
        raise AppError(
            "USE_LIVE_INGEST",
            "This broadcast is still live. Use the live form.",
            status_code=400,
        )
    duration = float(info.get("duration") or 0) if info else 0.0
    if duration > settings.max_source_duration_sec:
        raise AppError(
            "INGEST_FAILED",
            f"Source longer than {settings.max_source_duration_sec:g}s",
            status_code=400,
        )
    title = (payload.title or "").strip() or url
    project = _new_project(
        title=title[:512],
        source_type="url",
        source_url=url,
        source_meta={
            "webpage_url": url,
            "disclaimer_accepted_at": datetime.now(timezone.utc).isoformat(),
            "title_from_user": bool(payload.title and payload.title.strip()),
            "framing": parse_framing(payload.framing),
        },
        autopilot=payload.autopilot,
        clip_count=payload.clip_count,
    )
    if _needs_chunks(duration):
        _apply_chunks(project, duration)
        return await _enqueue_first_chunk(db, project)
    return await _enqueue_pipeline(db, project, "ingest")


def _new_project(
    *,
    title: str,
    source_type: str,
    source_meta: dict,
    source_url: str | None = None,
    autopilot: bool = False,
    clip_count: int | None = None,
) -> Project:
    settings = get_settings()
    default_count = settings.autopilot_clip_count if autopilot else settings.target_clip_count
    count = default_count if clip_count is None else clip_count
    if not 1 <= count <= 30:
        raise AppError("INVALID_CLIP_COUNT", "clip_count must be between 1 and 30", status_code=400)
    project = Project(
        id=str(uuid.uuid4()),
        title=title,
        status="ingesting",
        source_type=source_type,
        source_url=source_url,
        source_meta=source_meta,
        target_clip_count=count,
        autopilot=autopilot,
    )
    if autopilot:
        project.target_duration_sec = settings.autopilot_target_sec
    return project


def _form_int(raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise AppError("INVALID_CLIP_COUNT", "clip_count must be a whole number", status_code=400) from exc


async def _reexport_accepted(db: AsyncSession, project: Project) -> ProjectCreated:
    result = await db.execute(
        select(Clip).where(Clip.project_id == project.id, Clip.status.in_(("accepted", "exported")))
    )
    clips = list(result.scalars().all())
    if not clips:
        raise AppError("NOT_ACCEPTED", "Accept a clip before re-export", status_code=400)
    for clip in clips:
        await queue_clip_export(db, clip, force=True)
    project.status = "exporting"
    await db.commit()
    return ProjectCreated(id=project.id, status="exporting")


async def _enqueue_pipeline(db: AsyncSession, project: Project, from_stage: str) -> ProjectCreated:
    if not (project.source_type == "live" and project.status == "live"):
        project.status = "processing" if from_stage != "ingest" else "ingesting"
    job = Job(project_id=project.id, kind="pipeline", status="queued", stage="queued", progress=0.0)
    db.add(project)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await enqueue("run_pipeline", project.id, job.id, from_stage)
    return ProjectCreated(id=project.id, status=project.status)


async def _require_project(db: AsyncSession, project_id: str) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("NOT_FOUND", "Project not found", status_code=404)
    return project


async def _latest_job(db: AsyncSession, project_id: str) -> Job | None:
    """Prefer the job that is actually running. A newer queued window must not hide it."""
    running = await db.execute(
        select(Job)
        .where(Job.project_id == project_id, Job.status == "running")
        .order_by(Job.created_at.asc())
        .limit(1)
    )
    active = running.scalar_one_or_none()
    if active:
        return active
    result = await db.execute(
        select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


@router.patch("/projects/{project_id}/framing", response_model=ProjectOut)
async def update_framing(project_id: str, body: FramingUpdate, db: AsyncSession = Depends(get_db)) -> ProjectOut:
    """Switch face tracking or a center gameplay frame. Finished automatic shorts are rendered again."""
    project = await _require_project(db, project_id)
    framing = parse_framing(body.framing)
    previous = framing_of(project.source_meta)
    meta = dict(project.source_meta or {})
    meta["framing"] = framing
    project.source_meta = meta
    redo: list[Clip] = []
    if framing != previous:
        mode = crop_for_framing(framing)["mode"]
        result = await db.execute(select(Clip).where(Clip.project_id == project.id, Clip.status != "rejected"))
        for clip in result.scalars().all():
            current = str((clip.crop or {}).get("mode") or "face")
            if current not in {"face", "center", "full"}:
                continue
            clip.crop = {"mode": mode}
            ready = await db.execute(
                select(ClipExport.id).where(ClipExport.clip_id == clip.id, ClipExport.status == "ready").limit(1)
            )
            if ready.scalar_one_or_none():
                redo.append(clip)
    await db.commit()
    for clip in redo:
        await queue_clip_export(db, clip, force=True)
    await db.refresh(project)
    return await _project_out(db, project)


async def _project_out(db: AsyncSession, project: Project) -> ProjectOut:
    job = await _latest_job(db, project.id)
    out = ProjectOut.model_validate(project)
    out.framing = framing_of(project.source_meta)
    if job:
        out.latest_job = JobOut.model_validate(job)
    if project.autopilot:
        out.autopilot_summary = await autopilot_summary(db, project)
    out.live = await live_payload(db, project)
    counts = chunk_counts(project)
    if counts:
        out.chunks = ChunkStatus(**counts, error=(project.source_meta or {}).get("chunk_error"))
    return out


def _safe_duration(path: Path) -> float:
    try:
        return media_duration(ffprobe(path))
    except Exception:
        return 0.0


def _needs_chunks(duration: float) -> bool:
    """Split only long videos. Under the threshold one transcript and one ranking see the whole video,
    and the per-chunk fixed costs (proxy, ASR warm-up, two LLM calls) are paid once."""
    settings = get_settings()
    return duration > max(settings.live_chunk_sec, settings.vod_chunk_threshold_sec)


def _apply_chunks(project: Project, duration: float) -> None:
    settings = get_settings()
    pairs = plan_ranges(
        duration,
        chunk=settings.live_chunk_sec,
        step=settings.live_chunk_sec - settings.live_chunk_overlap_sec,
        min_tail=settings.clip_min_sec,
        max_duration=float(settings.max_source_duration_sec),
    )
    meta = dict(project.source_meta or {})
    meta["chunked"] = True
    meta["source_duration"] = duration
    meta["chunks"] = [
        {"index": index, "start": start, "end": end, "status": "planned"}
        for index, (start, end) in enumerate(pairs, start=1)
    ]
    project.source_meta = meta
    project.status = "processing"


async def _enqueue_first_chunk(db: AsyncSession, project: Project) -> ProjectCreated:
    db.add(project)
    await db.commit()
    await db.refresh(project)
    try:
        await enqueue("run_fetch_chunk", project.id, "1", queue=FETCH_QUEUE)
    except Exception as exc:
        meta = dict(project.source_meta or {})
        meta["chunk_error"] = "Could not queue the first chunk."
        project.source_meta = meta
        await db.commit()
        raise AppError(
            "INGEST_FAILED",
            "Could not queue the first chunk. Check that Redis and the fetcher are running.",
            status_code=503,
        ) from exc
    return ProjectCreated(id=project.id, status=project.status)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
