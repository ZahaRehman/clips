import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.base import Base
from app.models import Clip, Job, Project
from app.errors import AppError
from app.pipeline.export import resolve_export_source
from app.pipeline.llm_rank import parse_json_object, validate_clips
from app.pipeline.score import score_candidates
from app.pipeline.url_ingest import yt_dlp_argv
from app.pipeline.windows import run_propose_windows, snap_window, starts_inside_word


def _fixture_words() -> list[dict]:
    words: list[dict] = []
    t = 0.0
    plain = ["and", "the", "work", "continues", "today", "with", "care", "and", "focus", "now."]
    i = 0
    while t < 420:
        if i > 0 and i % 80 == 0:
            t += 0.7
        slot = i % 100
        hotspot = {
            0: "Why",
            1: "this",
            2: "mistake",
            3: "is",
            4: "a",
            5: "secret",
            6: "lesson?",
        }
        token = hotspot.get(slot, plain[i % len(plain)])
        words.append({"word": token, "start": round(t, 3), "end": round(t + 0.28, 3)})
        t += 0.36
        i += 1
    return words


class ScoreTests(unittest.TestCase):
    def test_fixture_yields_bounded_candidates(self) -> None:
        clips = score_candidates(
            _fixture_words(),
            min_sec=20,
            max_sec=60,
            target_sec=40,
            count=8,
            media_duration=430,
        )
        self.assertGreaterEqual(len(clips), 5)
        for clip in clips:
            dur = clip["end_sec"] - clip["start_sec"]
            self.assertGreaterEqual(dur, 20 - 0.05)
            self.assertLessEqual(dur, 60 + 0.05)
            self.assertLess(clip["start_sec"], clip["end_sec"])
            self.assertTrue(clip["title"])
            self.assertTrue(clip["reason"])
            self.assertGreaterEqual(clip["score"], 0)
            self.assertLessEqual(clip["score"], 100)
        for i, left in enumerate(clips):
            for right in clips[i + 1 :]:
                overlap = min(left["end_sec"], right["end_sec"]) - max(left["start_sec"], right["start_sec"])
                self.assertLessEqual(overlap, 5.05)


class WindowTests(unittest.TestCase):
    def test_snap_does_not_start_mid_word(self) -> None:
        words = [
            {"word": "hello", "start": 1.0, "end": 1.4},
            {"word": "there", "start": 1.45, "end": 1.9},
            {"word": "friend.", "start": 2.6, "end": 3.1},
            {"word": "Why", "start": 4.0, "end": 4.4},
            {"word": "now?", "start": 4.5, "end": 5.0},
        ]
        start, end = snap_window(1.2, 4.7, words, media_duration=8)
        self.assertFalse(starts_inside_word(start, words))
        self.assertGreater(end, start)
        self.assertLess(start, 1.05)

    def test_propose_replaces_only_proposed(self) -> None:
        tmp = tempfile.mkdtemp()
        previous = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = tmp
        get_settings.cache_clear()
        try:
            engine = create_engine("sqlite://")
            Base.metadata.create_all(engine)
            session = sessionmaker(bind=engine)()
            project_id = str(uuid.uuid4())
            project = Project(
                id=project_id,
                title="fixture",
                status="processing",
                source_type="upload",
                source_meta={"duration": 8},
            )
            job = Job(project_id=project_id, kind="pipeline", status="running", stage="asr")
            kept = Clip(
                project_id=project_id,
                t_start=1.0,
                t_end=1.9,
                title="keep me",
                status="accepted",
                rank=0,
            )
            session.add(project)
            session.add(job)
            session.add(kept)
            session.commit()

            from app.pipeline.paths import transcript_dir

            tdir = transcript_dir(project_id)
            (tdir / "words.json").write_text(
                '[{"word":"hello","start":1.0,"end":1.4},{"word":"there","start":1.5,"end":1.9},'
                '{"word":"Why","start":4.0,"end":4.3},{"word":"now?","start":4.4,"end":4.9}]',
                encoding="utf-8",
            )
            (tdir / "candidates.json").write_text(
                '{"clips":[{"start_sec":4.15,"end_sec":4.7,"score":80,"title":"Why now?","reason":"question"}]}',
                encoding="utf-8",
            )
            run_propose_windows(session, project, job)
            session.expire_all()
            rows = session.query(Clip).filter(Clip.project_id == project_id).all()
            accepted = [row for row in rows if row.status == "accepted"]
            proposed = [row for row in rows if row.status == "proposed"]
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0].id, kept.id)
            self.assertEqual(accepted[0].title, "keep me")
            self.assertEqual(len(proposed), 1)
            self.assertFalse(starts_inside_word(proposed[0].t_start, [
                {"word": "hello", "start": 1.0, "end": 1.4},
                {"word": "there", "start": 1.5, "end": 1.9},
                {"word": "Why", "start": 4.0, "end": 4.3},
                {"word": "now?", "start": 4.4, "end": 4.9},
            ]))
        finally:
            if previous is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = previous
            get_settings.cache_clear()
            shutil.rmtree(tmp, ignore_errors=True)


class LlmParseTests(unittest.TestCase):
    def test_provider_none_skips_network(self) -> None:
        from app.pipeline.llm_rank import rank_with_llm

        previous = os.environ.get("LLM_PROVIDER")
        os.environ["LLM_PROVIDER"] = "none"
        get_settings.cache_clear()
        try:
            self.assertEqual(
                rank_with_llm(
                    [{"start": 0, "end": 30, "text": "Why this mistake is a lesson."}],
                    min_sec=20,
                    max_sec=60,
                    target_count=4,
                    media_duration=120,
                ),
                (None, None),
            )
        finally:
            if previous is None:
                os.environ.pop("LLM_PROVIDER", None)
            else:
                os.environ["LLM_PROVIDER"] = previous
            get_settings.cache_clear()

    def test_ollama_down_reports_error(self) -> None:
        from app.pipeline.llm_rank import rank_with_llm

        saved = {key: os.environ.get(key) for key in ("LLM_PROVIDER", "OLLAMA_BASE_URL", "LLM_TIMEOUT_SEC")}
        os.environ["LLM_PROVIDER"] = "ollama"
        os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
        os.environ["LLM_TIMEOUT_SEC"] = "2"
        get_settings.cache_clear()
        try:
            self.assertEqual(
                rank_with_llm(
                    [{"start": 0, "end": 30, "text": "Why this mistake is a lesson."}],
                    min_sec=20,
                    max_sec=60,
                    target_count=4,
                    media_duration=120,
                ),
                (None, "LLM_HTTP"),
            )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            get_settings.cache_clear()

class LlmParseTestsRest(unittest.TestCase):
    def test_repairs_fenced_json(self) -> None:
        payload = parse_json_object('```json\n{"clips":[{"start_sec":1,"end_sec":30}]}\n```')
        self.assertIn("clips", payload)

    def test_drops_invalid_windows(self) -> None:
        clips = validate_clips(
            [
                {"start_sec": 1, "end_sec": 25, "score": 90, "title": "Ok", "reason": "fine"},
                {"start_sec": 10, "end_sec": 12, "title": "too short"},
                {"start_sec": 40, "end_sec": 90, "title": "too long"},
                {"start_sec": -2, "end_sec": 30, "title": "negative"},
                {"start_sec": 100, "end_sec": 140, "title": "past end"},
            ],
            min_sec=20,
            max_sec=60,
            media_duration=80,
        )
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["title"], "Ok")


class UrlIngestTests(unittest.TestCase):
    def test_argv_has_no_bypass_flags(self) -> None:
        argv = yt_dlp_argv("https://example.com/watch?v=abc", Path("/tmp/source.%(ext)s"))
        joined = " ".join(argv)
        for flag in (
            "--cookies",
            "--cookies-from-browser",
            "--username",
            "--password",
            "--allow-unplayable-formats",
            "--extractor-args",
            "--netrc",
        ):
            self.assertNotIn(flag, joined)
        self.assertIn("--max-filesize", argv)
        self.assertIn("--match-filter", argv)


class ExportSourceTests(unittest.TestCase):
    def test_proxy_fallback_and_missing_file(self) -> None:
        from app import storage as storage_mod
        from app.pipeline import paths

        root = Path(tempfile.mkdtemp())
        project_id = "export-src"
        settings = get_settings().model_copy(update={"data_dir": str(root), "storage_backend": "local"})
        previous_paths = paths.get_settings
        previous_storage = storage_mod.get_settings
        paths.get_settings = lambda: settings
        storage_mod.get_settings = lambda: settings
        storage_mod.reset_storage()
        try:
            with self.assertRaises(AppError) as missing:
                resolve_export_source(project_id)
            self.assertEqual(missing.exception.code, "EXPORT_FAILED")

            proxy = root / "projects" / project_id / "proxy" / "proxy.mp4"
            proxy.parent.mkdir(parents=True, exist_ok=True)
            proxy.write_bytes(b"proxy")
            self.assertEqual(resolve_export_source(project_id).name, "proxy.mp4")

            raw = root / "projects" / project_id / "raw"
            raw.mkdir()
            (raw / "source.mp4").write_bytes(b"raw")
            self.assertEqual(resolve_export_source(project_id).name, "source.mp4")
        finally:
            paths.get_settings = previous_paths
            storage_mod.get_settings = previous_storage
            storage_mod.reset_storage()
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
