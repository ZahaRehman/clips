"""Phase 9: range plan, section-download argv, and the long-URL create path."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.base import Base
from app.errors import AppError
from app.live.argv import _BANNED
from app.pipeline.ranges import plan_ranges
from app.vod.argv import section_argv
from app.vod.schedule import FETCH_QUEUE


class RangeTests(unittest.TestCase):
    def test_eleven_hour_vod_last_pair(self) -> None:
        ranges = plan_ranges(41601, chunk=300, step=280, min_tail=5, max_duration=43200)
        self.assertEqual(len(ranges), 149)
        self.assertEqual(ranges[0], (0.0, 300.0))
        self.assertEqual(ranges[-1], (41440.0, 41601.0))

    def test_one_chunk_is_a_single_range(self) -> None:
        """Create uses this only when duration is greater than one chunk."""
        self.assertEqual(plan_ranges(300, chunk=300, step=280, min_tail=5, max_duration=43200), [(0.0, 300.0)])

    def test_ten_second_tail_stays_inside_the_last_window(self) -> None:
        """The 20s overlap already covers a 10s tail, so it is not its own job."""
        kept = plan_ranges(570, chunk=300, step=280, min_tail=5, max_duration=43200)
        self.assertEqual(kept[-1], (280.0, 570.0))
        self.assertEqual(plan_ranges(4, chunk=300, step=280, min_tail=5, max_duration=43200), [])

    def test_over_twelve_hours_is_refused(self) -> None:
        with self.assertRaises(AppError) as caught:
            plan_ranges(43201, chunk=300, step=280, min_tail=5, max_duration=43200)
        self.assertIn("43200", caught.exception.message)


class SectionArgvTests(unittest.TestCase):
    def test_section_download_has_no_whole_video_filter_or_bypass(self) -> None:
        argv = section_argv("https://example.com/watch?v=abc", Path("/tmp/0001.mp4"), 280, 580)
        self.assertIn("--download-sections", argv)
        self.assertIn("*280.000-580.000", argv)
        self.assertNotIn("--match-filter", argv)
        self.assertNotIn("--force-keyframes-at-cuts", argv)
        for banned in _BANNED - {"--download-sections"}:
            self.assertNotIn(banned, argv)


class LongUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self._saved = {key: os.environ.get(key) for key in ("DATABASE_URL", "DATA_DIR", "ALLOW_URL_INGEST", "API_TOKEN")}
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.tmp}/api.db"
        os.environ["DATA_DIR"] = self.tmp
        os.environ["ALLOW_URL_INGEST"] = "true"
        os.environ["API_TOKEN"] = ""
        get_settings.cache_clear()
        import app.db.session as session_mod

        engine = create_async_engine(os.environ["DATABASE_URL"])
        session_mod.engine = engine
        session_mod.SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        Base.metadata.create_all(create_engine(f"sqlite:///{self.tmp}/api.db"))
        from app.main import app
        from fastapi.testclient import TestClient

        self.client_cm = TestClient(app)
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

    def test_long_finished_url_queues_one_chunk_and_not_the_whole_file(self) -> None:
        enqueue = AsyncMock()
        info = {"live_status": "was_live", "duration": 3600}
        with (
            patch("app.live.probe.peek_url", return_value=info),
            patch("app.api.routes.projects.enqueue", enqueue),
            patch("app.pipeline.url_ingest.download_url", side_effect=AssertionError("whole file")),
        ):
            response = self.client.post(
                "/api/v1/projects",
                json={
                    "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "disclaimer_accepted": True,
                    "framing": "gameplay",
                },
            )
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "processing")
        self.assertEqual(enqueue.await_args.args[0], "run_fetch_chunk")
        self.assertEqual(enqueue.await_args.args[2], "1")
        self.assertEqual(enqueue.await_args.kwargs["queue"], FETCH_QUEUE)
        project = self.client.get(f"/api/v1/projects/{body['id']}")
        self.assertEqual(project.status_code, 200)
        chunks = project.json()["chunks"]
        self.assertEqual(chunks["total"], 13)
        self.assertEqual(chunks["planned"], 13)
        self.assertEqual(project.json()["framing"], "gameplay")

    def test_fifteen_minute_url_runs_as_one_file(self) -> None:
        enqueue = AsyncMock()
        with (
            patch("app.live.probe.peek_url", return_value={"live_status": "not_live", "duration": 900}),
            patch("app.api.routes.projects.enqueue", enqueue),
        ):
            response = self.client.post(
                "/api/v1/projects",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "disclaimer_accepted": True},
            )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(enqueue.await_args.args[0], "run_pipeline")
        self.assertIsNone(self.client.get(f"/api/v1/projects/{response.json()['id']}").json()["chunks"])

    def test_short_url_stays_one_shot(self) -> None:
        enqueue = AsyncMock()
        with (
            patch("app.live.probe.peek_url", return_value={"live_status": "not_live", "duration": 120}),
            patch("app.api.routes.projects.enqueue", enqueue),
        ):
            response = self.client.post(
                "/api/v1/projects",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "disclaimer_accepted": True},
            )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(enqueue.await_args.args[0], "run_pipeline")
        self.assertIsNone(enqueue.await_args.kwargs.get("queue"))

    def test_over_twelve_hours_does_not_start_a_plan(self) -> None:
        enqueue = AsyncMock()
        with (
            patch("app.live.probe.peek_url", return_value={"live_status": "was_live", "duration": 43201}),
            patch("app.api.routes.projects.enqueue", enqueue),
        ):
            response = self.client.post(
                "/api/v1/projects",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "disclaimer_accepted": True},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INGEST_FAILED")
        enqueue.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
