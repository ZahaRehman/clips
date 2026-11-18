"""Start and stop the capture process. The pipeline worker does not own this."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from app.db.sync import sync_session
from app.live.jobs import RESUME_MESSAGE, finish_capture
from app.models import LiveWindow, Project
from app.pipeline.paths import project_dir


def stop_path(project_id: str) -> Path:
    folder = project_dir(project_id) / "capture"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "STOP"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start_supervisor(project_id: str, *, resumed: bool) -> int:
    if resumed:
        _note_resume(project_id)
    log_path = project_dir(project_id) / "capture" / "supervisor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.live.supervisor", project_id],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        log_file.close()
    _store_pid(project_id, proc.pid)
    return proc.pid


def request_stop(project_id: str) -> None:
    """Ask the supervisor to exit. A dead supervisor is finished here so a new capture can start."""
    path = stop_path(project_id)
    path.write_text("stop\n", encoding="utf-8")
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return
        meta = dict(project.source_meta or {})
        meta["live_state"] = "stopping"
        project.source_meta = meta
        session.add(project)
        session.commit()
        pid = int(meta.get("supervisor_pid") or 0)
    if pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        return
    with sync_session() as session:
        project = session.get(Project, project_id)
        if project and project.status == "live":
            finish_capture(session, project, state="stopped")


def resume_orphaned() -> None:
    """After an API restart, a live project with a dead supervisor continues at the live edge."""
    if os.environ.get("CLIPING_SKIP_LIVE_RESUME") == "1":
        return
    try:
        with sync_session() as session:
            rows = session.query(Project).filter(Project.status == "live").all()
            pending = []
            for project in rows:
                pid = int((project.source_meta or {}).get("supervisor_pid") or 0)
                if pid_alive(pid):
                    continue
                pending.append(project.id)
    except Exception:
        return
    for project_id in pending:
        try:
            start_supervisor(project_id, resumed=True)
        except Exception:
            continue


def _note_resume(project_id: str) -> None:
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return
        has_window = (
            session.query(LiveWindow.id).filter(LiveWindow.project_id == project_id).first() is not None
        )
        meta = dict(project.source_meta or {})
        meta["resume_pending"] = True
        meta["live_state"] = "starting"
        if has_window:
            meta["live_message"] = RESUME_MESSAGE
        project.source_meta = meta
        session.add(project)
        session.commit()


def _store_pid(project_id: str, pid: int) -> None:
    with sync_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return
        meta = dict(project.source_meta or {})
        meta["supervisor_pid"] = pid
        meta["live_state"] = meta.get("live_state") or "starting"
        project.source_meta = meta
        session.add(project)
        session.commit()
