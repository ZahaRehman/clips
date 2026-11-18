"""Phase 8: live status, closed windows, and the live route gates."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.base import Base
from app.errors import AppError
from app.live.argv import live_follow_argv, probe_argv
from app.live.cut import advance_cut, closed_span, copy_window, growing_duration, tail_span
from app.live.folder import SeenFile, take_stable
from app.live.jobs import finish_capture, mark_window
from app.live.overlap import reject_overlapping_proposals
from app.live.status import refusal_for
from app.models import Clip, Job, LiveWindow, Project
from app.pipeline.ingest import media_source
from app.pipeline.windows import run_propose_windows


class StatusTests(unittest.TestCase):
    def test_five_live_status_strings(self) -> None:
        self.assertIsNone(refusal_for("is_live"))
        code, message = refusal_for("is_upcoming") or ("", "")
        self.assertEqual(message, "This premiere has not started.")
        self.assertEqual(code, "LIVE_NOT_STARTED")
        code, message = refusal_for("post_live") or ("", "")
        self.assertEqual(message, "YouTube is still turning this into a video. Wait, then use the normal URL form.")
        code, message = refusal_for("was_live") or ("", "")
        self.assertEqual(message, "This is not live. Use the normal URL form.")
        code, message = refusal_for("not_live") or ("", "")
        self.assertEqual(message, "This is not live. Use the normal URL form.")


class ArgvTests(unittest.TestCase):
    def test_live_argv_has_no_cookie_or_bypass(self) -> None:
        follow = live_follow_argv("https://www.youtube.com/watch?v=aaaaaaaaaaa", Path("/data/p/capture/follow.mp4"))
        probe = probe_argv("https://www.youtube.com/watch?v=aaaaaaaaaaa")
        for argv in (follow, probe):
            self.assertNotIn("--cookies", argv)
            self.assertNotIn("--cookies-from-browser", argv)
            self.assertNotIn("--username", argv)
            self.assertNotIn("--password", argv)
            self.assertNotIn("--netrc", argv)
            self.assertNotIn("--download-sections", argv)
            self.assertNotIn("--live-from-start", argv)
            self.assertIn("--no-live-from-start", argv)
        self.assertNotIn("--merge-output-format", follow)
        self.assertNotIn("--match-filter", follow)
        self.assertIn("--no-download", probe)
        self.assertTrue(str(follow[follow.index("-o") + 1]).endswith("follow.mp4"))


class CutPlanTests(unittest.TestCase):
    def test_closed_window_stays_behind_the_edge(self) -> None:
        self.assertIsNone(closed_span(0, 319, 300, 20))
        self.assertEqual(closed_span(0, 320, 300, 20), (0, 300))
        self.assertIsNone(closed_span(0, 300, 300, 0))
        last = advance_cut(0, 300, 20)
        self.assertEqual(last, 280)
        self.assertIsNone(tail_span(280, 290, 20))
        self.assertEqual(tail_span(280, 305, 20), (280, 305))


class OverlapTests(unittest.TestCase):
    def test_overlap_keeps_higher_score_and_failed_window_keeps_other_clips(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        project = Project(id=str(uuid.uuid4()), title="live", status="live", source_type="live")
        session.add(project)
        session.commit()
        first = Clip(
            project_id=project.id,
            t_start=0,
            t_end=40,
            score=90,
            title="first",
            status="proposed",
            stream_offset_sec=0,
            rank=1,
        )
        second = Clip(
            project_id=project.id,
            t_start=0,
            t_end=40,
            score=40,
            title="second",
            status="proposed",
            stream_offset_sec=10,
            rank=2,
        )
        kept = Clip(
            project_id=project.id,
            t_start=0,
            t_end=20,
            score=10,
            title="other window",
            status="proposed",
            stream_offset_sec=400,
            rank=3,
        )
        window_id = str(uuid.uuid4())
        window = LiveWindow(
            id=window_id,
            project_id=project.id,
            index=2,
            stream_start_sec=10,
            stream_end_sec=50,
            path="projects/x/capture/windows/0002.mp4",
            status="running",
        )
        job = Job(
            project_id=project.id,
            kind="pipeline",
            status="running",
            checkpoint={"live_window": {"id": window_id, "index": 2, "stream_start_sec": 10}},
        )
        session.add_all([first, second, kept, window, job])
        session.commit()

        changed = reject_overlapping_proposals(session, project.id)
        self.assertEqual(changed, 1)
        session.expire_all()
        rows = {row.title: row for row in session.query(Clip).all()}
        self.assertEqual(rows["first"].status, "proposed")
        self.assertEqual(rows["second"].status, "rejected")
        self.assertEqual(rows["second"].reason, "overlap")
        self.assertEqual(rows["other window"].status, "proposed")

        mark_window(session, job, "failed", "asr failed")
        session.expire_all()
        self.assertEqual(session.get(LiveWindow, window.id).status, "failed")
        self.assertEqual(session.get(LiveWindow, window.id).error, "asr failed")
        self.assertEqual(session.query(Clip).count(), 3)
        finish_capture(session, project, state="stopped")
        session.expire_all()
        self.assertEqual(session.query(Clip).count(), 3)
        self.assertEqual(session.get(Project, project.id).status, "ready_for_review")


class WindowProposeTests(unittest.TestCase):
    def test_rerun_replaces_only_that_window(self) -> None:
        tmp = tempfile.mkdtemp()
        previous = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = tmp
        get_settings.cache_clear()
        try:
            engine = create_engine("sqlite://")
            session = sessionmaker(bind=engine)()
            Base.metadata.create_all(engine)
            project_id = str(uuid.uuid4())
            project = Project(
                id=project_id,
                title="live",
                status="live",
                source_type="live",
                source_meta={"duration": 30},
            )
            job = Job(
                project_id=project_id,
                kind="pipeline",
                status="running",
                checkpoint={"live_window": {"id": "w2", "index": 2, "stream_start_sec": 280.0}},
            )
            earlier = Clip(
                project_id=project_id,
                t_start=1,
                t_end=20,
                score=50,
                title="keep",
                status="proposed",
                stream_offset_sec=0,
                rank=1,
            )
            session.add_all([project, job, earlier])
            session.commit()
            from app.pipeline.paths import transcript_dir

            tdir = transcript_dir(project_id)
            (tdir / "words.json").write_text(
                '[{"word":"Why","start":1.0,"end":1.4},{"word":"now?","start":1.5,"end":2.2}]',
                encoding="utf-8",
            )
            (tdir / "candidates.json").write_text(
                '{"clips":[{"start_sec":1.0,"end_sec":2.2,"score":70,"title":"Next","reason":"hook"}]}',
                encoding="utf-8",
            )
            run_propose_windows(session, project, job)
            session.expire_all()
            rows = session.query(Clip).filter(Clip.project_id == project_id).all()
            kept = [row for row in rows if row.stream_offset_sec == 0]
            fresh = [row for row in rows if row.stream_offset_sec == 280]
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].title, "keep")
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0].title, "Next")
            self.assertIsNone(session.query(Clip).filter(Clip.project_id == project_id, Clip.stream_offset_sec.is_(None)).first())
        finally:
            if previous is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = previous
            get_settings.cache_clear()
            shutil.rmtree(tmp, ignore_errors=True)


class FollowGuardTests(unittest.TestCase):
    def test_follow_file_is_not_a_window_source(self) -> None:
        root = Path(tempfile.mkdtemp())
        previous = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = str(root)
        get_settings.cache_clear()
        try:
            follow = root / "projects" / "p" / "capture" / "follow.mp4.part"
            follow.parent.mkdir(parents=True)
            follow.write_bytes(b"nope")
            window = root / "projects" / "p" / "capture" / "windows" / "0001.mp4"
            window.parent.mkdir(parents=True)
            window.write_bytes(b"window")
            job = Job(
                project_id="p",
                checkpoint={"live_window": {"path": "projects/p/capture/follow.mp4.part"}},
            )
            with self.assertRaises(AppError) as caught:
                media_source("p", job)
            self.assertIn("follow", caught.exception.message)
            job.checkpoint = {"live_window": {"path": "projects/p/capture/windows/0001.mp4"}}
            self.assertEqual(media_source("p", job).name, "0001.mp4")
        finally:
            if previous is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = previous
            get_settings.cache_clear()
            shutil.rmtree(root, ignore_errors=True)


class FolderTests(unittest.TestCase):
    def test_growing_file_is_ignored_until_size_is_stable(self) -> None:
        growing = Path("/tmp/growing.mp4")
        stable = Path("/tmp/stable.mp4")
        seen: dict[str, SeenFile] = {}
        ready = take_stable([(growing, 10), (stable, 20)], seen, now=0, stable_sec=10, already=set())
        self.assertEqual(ready, [])
        ready = take_stable([(growing, 15), (stable, 20)], seen, now=11, stable_sec=10, already=set())
        self.assertEqual(ready, [stable])


class GrowingFileTests(unittest.TestCase):
    def test_two_closed_windows_and_a_tail_while_the_writer_runs(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg is not installed")
        root = Path(tempfile.mkdtemp())
        partial = root / "follow.mp4.part"
        saved = {key: os.environ.get(key) for key in ("LIVE_CHUNK_SEC", "LIVE_CHUNK_OVERLAP_SEC", "LIVE_EDGE_LAG_SEC", "CLIP_MIN_SEC", "FFMPEG_PATH", "FFPROBE_PATH")}
        os.environ["LIVE_CHUNK_SEC"] = "2"
        os.environ["LIVE_CHUNK_OVERLAP_SEC"] = "0.5"
        os.environ["LIVE_EDGE_LAG_SEC"] = "1"
        os.environ["CLIP_MIN_SEC"] = "1"
        os.environ["FFMPEG_PATH"] = ffmpeg
        os.environ["FFPROBE_PATH"] = shutil.which("ffprobe") or "ffprobe"
        get_settings.cache_clear()
        proc = subprocess.Popen(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x120:rate=8",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=8000",
                "-t",
                "8",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-g",
                "8",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-flush_packets",
                "1",
                "-f",
                "mpegts",
                str(partial),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        made: list[Path] = []
        last = 0.0
        try:
            deadline = time.monotonic() + 20
            while len(made) < 2 and time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                duration = growing_duration(partial)
                span = closed_span(last, duration, 2, 1)
                if span is None:
                    time.sleep(0.25)
                    continue
                start, end = span
                dest = root / f"{len(made) + 1:04d}.mp4"
                copy_window(partial, start, end - start, dest)
                made.append(dest)
                last = advance_cut(last, 2, 0.5)
            self.assertIsNone(proc.poll(), "writer exited before two windows were cut")
            self.assertEqual(len(made), 2)
            for path in made:
                self.assertNotIn("follow", path.name)
                self.assertGreater(growing_duration(path), 0)
            proc.wait(timeout=20)
            final = partial if partial.is_file() else root / "follow.mp4"
            duration = growing_duration(final)
            span = tail_span(last, duration, 1)
            self.assertIsNotNone(span)
            assert span is not None
            tail = root / "0003.mp4"
            copy_window(final, span[0], span[1] - span[0], tail)
            self.assertGreater(growing_duration(tail), 0)
            self.assertEqual(sorted(path.name for path in made), ["0001.mp4", "0002.mp4"])
            self.assertTrue(tail.is_file())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            get_settings.cache_clear()
            shutil.rmtree(root, ignore_errors=True)


class MigrationTests(unittest.TestCase):
    def test_upgrade_from_phase5_keeps_existing_rows(self) -> None:
        from alembic import command
        from alembic.config import Config

        root = Path(tempfile.mkdtemp())
        db = root / "clip.db"
        previous = {key: os.environ.get(key) for key in ("DATABASE_URL", "DATA_DIR")}
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db}"
        os.environ["DATA_DIR"] = str(root)
        get_settings.cache_clear()
        api = Path(__file__).resolve().parents[1]
        cwd = Path.cwd()
        try:
            os.chdir(api)
            cfg = Config(str(api / "alembic.ini"))
            command.upgrade(cfg, "20261115_0005")
            engine = create_engine(f"sqlite:///{db}")
            with engine.begin() as conn:
                project_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO projects (id, title, status, source_type, target_clip_count, target_duration_sec, autopilot) "
                        "VALUES (:id, 'old', 'ready_for_review', 'upload', 8, 45, 0)"
                    ),
                    {"id": project_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO clips (id, project_id, rank, t_start, t_end, score, title, reason, status) "
                        "VALUES (:id, :project, 1, 1, 12, 10, 'old clip', '', 'accepted')"
                    ),
                    {"id": str(uuid.uuid4()), "project": project_id},
                )
            get_settings.cache_clear()
            command.upgrade(cfg, "head")
            with engine.connect() as conn:
                offset = conn.execute(text("SELECT stream_offset_sec FROM clips")).scalar()
                count = conn.execute(text("SELECT COUNT(*) FROM live_windows")).scalar()
                title = conn.execute(text("SELECT title FROM projects")).scalar()
            self.assertIsNone(offset)
            self.assertEqual(count, 0)
            self.assertEqual(title, "old")
        finally:
            os.chdir(cwd)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            get_settings.cache_clear()
            shutil.rmtree(root, ignore_errors=True)


class LiveRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self._saved = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DATA_DIR", "ALLOW_LIVE_INGEST", "ALLOW_URL_INGEST", "CLIPING_SKIP_LIVE_RESUME", "API_TOKEN")
        }
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.tmp}/api.db"
        os.environ["DATA_DIR"] = self.tmp
        os.environ["ALLOW_LIVE_INGEST"] = "false"
        os.environ["ALLOW_URL_INGEST"] = "true"
        os.environ["CLIPING_SKIP_LIVE_RESUME"] = "1"
        os.environ["API_TOKEN"] = ""
        get_settings.cache_clear()
        import app.db.session as session_mod

        engine = create_async_engine(os.environ["DATABASE_URL"])
        session_mod.engine = engine
        session_mod.SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        Base.metadata.create_all(create_engine(f"sqlite:///{self.tmp}/api.db"))
        from app.main import app

        self.client_cm = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_flag_is_403(self) -> None:
        response = self.client.post(
            "/api/v1/projects/live",
            json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "disclaimer_accepted": True},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "LIVE_INGEST_DISABLED")

    def test_missing_disclaimer_is_400(self) -> None:
        os.environ["ALLOW_LIVE_INGEST"] = "true"
        get_settings.cache_clear()
        response = self.client.post(
            "/api/v1/projects/live",
            json={"folder": self.tmp},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "DISCLAIMER_REQUIRED")

    def test_second_live_start_is_409(self) -> None:
        os.environ["ALLOW_LIVE_INGEST"] = "true"
        get_settings.cache_clear()
        engine = create_engine(f"sqlite:///{self.tmp}/api.db")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects (id, title, status, source_type, target_clip_count, target_duration_sec, autopilot) "
                    "VALUES (:id, 'busy', 'live', 'live', 8, 45, 0)"
                ),
                {"id": str(uuid.uuid4())},
            )
        response = self.client.post(
            "/api/v1/projects/live",
            json={"folder": self.tmp, "disclaimer_accepted": True},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "LIVE_BUSY")

    def test_url_form_refuses_is_live(self) -> None:
        with patch("app.live.probe.peek_url", return_value={"live_status": "is_live", "duration": 100}):
            response = self.client.post(
                "/api/v1/projects",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "disclaimer_accepted": True},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "USE_LIVE_INGEST")


if __name__ == "__main__":
    unittest.main()
