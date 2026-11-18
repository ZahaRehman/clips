import time

from app.db.sync import sync_session
from app.errors import AppError
from app.live.jobs import mark_window
from app.metrics import record_job
from app.models import Clip, ClipExport, Job, Project
from app.pipeline.stage_log import call_stage
from app.pipeline.asr import run_asr
from app.pipeline.export import run_export_clip
from app.pipeline.ffmpeg_util import sha256_file
from app.pipeline.ingest import media_source, run_ingest
from app.pipeline.job_state import set_project_status, update_job
from app.pipeline.normalize import abort_proxy, finish_proxy, run_normalize
from app.pipeline.paths import pop_window_index, push_window_index
from app.pipeline.post_metadata import run_post_metadata
from app.pipeline.render_clips import run_render_clips
from app.pipeline.segment_score import run_segment_score
from app.pipeline.windows import run_propose_windows
from app.vod.fetch import release_ready_chunk
from app.vod.schedule import is_chunked, maybe_finish, set_chunk_status

STAGES = (
    "ingest",
    "normalize",
    "asr",
    "segment_score",
    "propose_windows",
    "post_metadata",
    "render_clips",
)
AUTOPILOT_STAGES = ("post_metadata", "render_clips")


def run_pipeline(project_id: str, job_id: str, from_stage: str = "ingest") -> str:
    if from_stage not in STAGES:
        from_stage = "ingest"
    start = STAGES.index(from_stage)
    with sync_session() as session:
        project = session.get(Project, project_id)
        job = session.get(Job, job_id)
        if not project or not job:
            raise AppError("PIPELINE_FAILED", "Project or job missing", status_code=404)
        token = _push_live_scope(job)
        live_job = _live_job(project, job)
        try:
            if not live_job:
                set_project_status(session, project, "processing")
            else:
                mark_window(session, job, "running")
            update_job(session, job, status="running", stage=from_stage, progress=0.0, error="")
            checksum = _raw_checksum(job, project.id, need_file=start > 0)
            if start <= STAGES.index("ingest"):
                ingest_info = _timed_stage(session, job, project.id, "ingest", lambda: run_ingest(session, project, job))
                checksum = ingest_info["checksum"]
            if start <= STAGES.index("normalize"):
                _timed_stage(session, job, project.id, "normalize", lambda: run_normalize(session, project, job, checksum))
            if start <= STAGES.index("asr"):
                _timed_stage(session, job, project.id, "asr", lambda: run_asr(session, project, job, checksum))
            if start <= STAGES.index("segment_score"):
                _timed_stage(session, job, project.id, "segment_score", lambda: run_segment_score(session, project, job))
            if start <= STAGES.index("propose_windows"):
                _timed_stage(session, job, project.id, "propose_windows", lambda: run_propose_windows(session, project, job))
            if project.autopilot and start <= STAGES.index("post_metadata"):
                _timed_stage(session, job, project.id, "post_metadata", lambda: run_post_metadata(session, project, job))
            # Renders and the review player need the proxy; it was encoding during ASR and ranking.
            _timed_stage(session, job, project.id, "proxy_wait", lambda: finish_proxy(session, job.id))
            if project.autopilot and start <= STAGES.index("render_clips"):
                _timed_stage(session, job, project.id, "render_clips", lambda: run_render_clips(session, project, job))
            update_job(session, job, stage="done", status="succeeded", progress=1.0)
            if live_job:
                mark_window(session, job, "ready")
                session.refresh(project)
                if is_chunked(project):
                    _chunk_done(session, project, job)
                elif project.status != "live":
                    _promote_if_clips(session, project)
            else:
                set_project_status(session, project, "ready_for_review")
            record_job("succeeded")
            return "ok"
        except Exception as exc:
            abort_proxy(job.id)
            message = exc.message if isinstance(exc, AppError) else str(exc)
            update_job(session, job, status="failed", error=message)
            if live_job:
                mark_window(session, job, "failed", message)
                session.refresh(project)
                if is_chunked(project):
                    _chunk_failed(session, project, job, message)
            else:
                set_project_status(session, project, "failed")
            record_job("failed")
            raise
        finally:
            if token is not None:
                pop_window_index(token)


def _timed_stage(session, job: Job, project_id: str, stage: str, fn):
    """call_stage, plus wall seconds saved in checkpoint["timings"] so the DB keeps a per-stage history."""
    started = time.perf_counter()
    result = call_stage(project_id, job.id, stage, fn)
    checkpoint = dict(job.checkpoint or {})
    timings = dict(checkpoint.get("timings") or {})
    timings[stage] = round(time.perf_counter() - started, 1)
    checkpoint["timings"] = timings
    update_job(session, job, checkpoint=checkpoint)
    return result


def run_metadata(project_id: str, job_id: str) -> str:
    with sync_session() as session:
        project = session.get(Project, project_id)
        job = session.get(Job, job_id)
        if not project or not job:
            raise AppError("PIPELINE_FAILED", "Project or job missing", status_code=404)
        try:
            update_job(session, job, status="running", stage="post_metadata", progress=0.1)
            call_stage(project.id, job.id, "post_metadata", lambda: run_post_metadata(session, project, job))
            update_job(session, job, stage="done", status="succeeded", progress=1.0)
            record_job("succeeded")
            return "ok"
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            update_job(session, job, status="failed", error=message)
            record_job("failed")
            raise


def _raw_checksum(job: Job, project_id: str, need_file: bool) -> str:
    checkpoint = job.checkpoint or {}
    ingest = checkpoint.get("ingest") or {}
    checksum = str(ingest.get("checksum") or "")
    if checksum or not need_file:
        return checksum
    return sha256_file(media_source(project_id, job))


def _live_job(project: Project, job: Job) -> bool:
    if project.source_type == "live" and project.status == "live":
        return True
    return isinstance((job.checkpoint or {}).get("live_window"), dict)


def _push_live_scope(job: Job):
    live = (job.checkpoint or {}).get("live_window")
    if not isinstance(live, dict) or live.get("index") is None:
        return None
    return push_window_index(int(live["index"]))


def _chunk_done(session, project: Project, job: Job) -> None:
    live = (job.checkpoint or {}).get("live_window") or {}
    index = live.get("index")
    if index is not None:
        set_chunk_status(project, int(index), "ready")
        session.add(project)
        session.commit()
    release_ready_chunk(session, project, job)
    maybe_finish(session, project)


def _chunk_failed(session, project: Project, job: Job, message: str) -> None:
    live = (job.checkpoint or {}).get("live_window") or {}
    index = live.get("index")
    if index is not None:
        set_chunk_status(project, int(index), "failed", message)
        session.add(project)
        session.commit()
    maybe_finish(session, project)


def _promote_if_clips(session, project: Project) -> None:
    """A window that finishes after Stop still leaves its proposals reviewable."""
    if project.status != "done":
        return
    if session.query(Clip.id).filter(Clip.project_id == project.id).first() is None:
        return
    set_project_status(session, project, "ready_for_review")


def run_export(clip_id: str, export_id: str, job_id: str) -> str:
    with sync_session() as session:
        try:
            clip = session.get(Clip, clip_id)
            project_id = clip.project_id if clip else clip_id
            call_stage(project_id, job_id, "export", lambda: run_export_clip(session, clip_id, export_id, job_id))
            record_job("succeeded")
            return "ok"
        except Exception as exc:
            record_job("failed")
            session.rollback()
            message = exc.message if isinstance(exc, AppError) else str(exc)
            job = session.get(Job, job_id)
            export = session.get(ClipExport, export_id)
            if job:
                update_job(session, job, status="failed", error=message)
            if export:
                export.status = "failed"
                export.error = message[:2000]
                session.add(export)
                session.commit()
            clip = session.get(Clip, clip_id)
            if clip:
                project = session.get(Project, clip.project_id)
                if project and project.status == "exporting":
                    set_project_status(session, project, "ready_for_review")
            raise
