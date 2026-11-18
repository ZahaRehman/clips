"""One capture process per live project. Closed windows become normal pipeline jobs."""

from __future__ import annotations

import signal
import sys

from app.db.sync import sync_session
from app.live.capture import run_youtube
from app.live.folder import run_folder
from app.live.jobs import finish_capture
from app.logging_setup import get_logger
from app.models import Project

log = get_logger()


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv if argv is None else argv)
    if len(args) < 2:
        raise SystemExit("usage: python -m app.live.supervisor PROJECT_ID")
    run_supervisor(args[1])


def run_supervisor(project_id: str) -> str:
    stop = False

    def _handle(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    outcome = "running"
    error = ""
    try:
        with sync_session() as session:
            project = session.get(Project, project_id)
            if not project or project.source_type != "live":
                return "missing"
            mode = str((project.source_meta or {}).get("capture_mode") or "youtube")
        if mode == "folder":
            outcome, error = run_folder(project_id, lambda: stop)
        else:
            outcome, error = run_youtube(project_id, lambda: stop)
    except Exception as exc:
        log.exception("live supervisor crashed")
        outcome = "crashed"
        error = str(exc)[:500]
    if outcome == "crashed":
        # Leave status=live so the API can resume at the live edge.
        return outcome
    state = "failed" if outcome in {"disk", "error"} else "stopped"
    message = _message(outcome, error)
    with sync_session() as session:
        project = session.get(Project, project_id)
        if project and project.status == "live":
            finish_capture(session, project, state=state, message=message, error=error or None)
    return outcome


def _message(outcome: str, error: str) -> str | None:
    if outcome == "limit":
        return "Capture stopped at the LIVE_MAX_HOURS limit."
    if outcome == "stall":
        return "The capture file stopped growing."
    if outcome == "disk":
        return error or "Not enough free disk."
    if outcome == "error":
        return error or "Capture stopped."
    return None


if __name__ == "__main__":
    main()
