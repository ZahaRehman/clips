import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.errors import AppError
from app.pipeline.cleanup import cleanup_tree
from app.pipeline.disk import ensure_free_disk
from app.pipeline.export import export_input_hash
from app.pipeline.watermark import text_filter, watermark_token
from app.redact import scrub_text
from app.storage import get_storage, reset_storage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


def _hash(**overrides: object) -> str:
    base = {
        "t_start": 1.0,
        "t_end": 5.0,
        "crop": {"mode": "center", "x": 0.2, "y": 0.0, "w": 0.3, "h": 1.0},
        "words": [{"word": "Hi", "start": 1.0, "end": 1.2}],
        "preset": "shorts_1080",
        "width": 1080,
        "height": 1920,
    }
    base.update(overrides)
    return export_input_hash(**base)  # type: ignore[arg-type]


class StorageTests(unittest.TestCase):
    def test_local_round_trip_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStorage(Path(tmp))
            store.put("projects/p1/raw/a.txt", b"hi")
            self.assertEqual(store.get("projects/p1/raw/a.txt"), b"hi")
            with store.open("projects/p1/raw/a.txt") as handle:
                self.assertEqual(handle.read(), b"hi")
            self.assertEqual(store.url_for("projects/p1/raw/a.txt"), "/api/v1/files/p1/raw/a.txt")
            store.delete("projects/p1")
            self.assertFalse((Path(tmp) / "projects" / "p1").exists())
            with self.assertRaises(AppError):
                store.get("../secret")

    def test_s3_stub_does_not_touch_network(self) -> None:
        store = S3Storage(bucket="cliping", prefix="media")
        self.assertEqual(store.url_for("projects/p/raw/a.mp4"), "s3://cliping/media/projects/p/raw/a.mp4")
        with self.assertRaises(AppError) as caught:
            store.put("projects/p/raw/a.mp4", b"x")
        self.assertEqual(caught.exception.code, "STORAGE_UNAVAILABLE")
        with self.assertRaises(AppError):
            store.local_path("projects/p/raw/a.mp4")

    def test_factory_defaults_to_local(self) -> None:
        reset_storage()
        get_settings.cache_clear()
        try:
            self.assertIsInstance(get_storage(), LocalStorage)
        finally:
            reset_storage()
            get_settings.cache_clear()


class PostgresUrlTests(unittest.TestCase):
    def test_sqlite_and_postgres_sync_urls(self) -> None:
        sqlite = Settings(database_url="sqlite+aiosqlite:///./data/cliping.db")
        self.assertEqual(sqlite.sync_database_url, "sqlite:///./data/cliping.db")
        pg = Settings(database_url="postgresql+asyncpg://cliping:cliping@postgres:5432/cliping")
        self.assertEqual(pg.sync_database_url, "postgresql+psycopg://cliping:cliping@postgres:5432/cliping")
        plain = Settings(database_url="postgresql://cliping:cliping@postgres:5432/cliping")
        self.assertTrue(plain.sync_database_url.startswith("postgresql+psycopg://"))


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["API_TOKEN"] = "phase4-token"
        get_settings.cache_clear()
        from app.main import app

        self.client = TestClient(app)

    def tearDown(self) -> None:
        os.environ.pop("API_TOKEN", None)
        get_settings.cache_clear()

    def test_health_public_and_api_locked(self) -> None:
        health = self.client.get("/api/v1/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn("jobs_succeeded", health.json())
        locked = self.client.get("/api/v1/projects")
        self.assertEqual(locked.status_code, 401)
        self.assertEqual(locked.json()["error"]["code"], "UNAUTHORIZED")
        cleanup = self.client.post("/api/v1/admin/cleanup")
        self.assertEqual(cleanup.status_code, 401)
        header = self.client.get("/openapi.json", headers={"X-API-Token": "phase4-token"})
        bearer = self.client.get("/openapi.json", headers={"Authorization": "Bearer phase4-token"})
        self.assertEqual(header.status_code, 200)
        self.assertEqual(bearer.status_code, 200)


class CleanupAndDiskTests(unittest.TestCase):
    def test_cleanup_removes_temps_proxy_and_failed_partials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "projects" / "ok"
            raw = project / "raw" / "source.mp4"
            proxy = project / "proxy" / "proxy.mp4"
            temp = project / "raw" / "source.mp4.part"
            fresh = project / "audio" / "note.tmp"
            raw.parent.mkdir(parents=True)
            proxy.parent.mkdir(parents=True)
            fresh.parent.mkdir(parents=True)
            raw.write_bytes(b"raw")
            proxy.write_bytes(b"proxy")
            temp.write_bytes(b"part")
            fresh.write_bytes(b"new")
            failed_proxy = root / "projects" / "bad" / "proxy" / "proxy.mp4"
            failed_raw = root / "projects" / "bad" / "raw" / "source.mp4"
            failed_proxy.parent.mkdir(parents=True)
            failed_raw.parent.mkdir(parents=True)
            failed_proxy.write_bytes(b"old-proxy")
            failed_raw.write_bytes(b"keep")
            old = time.time() - 7200
            os.utime(temp, (old, old))
            os.utime(proxy, (old, old))
            stats = cleanup_tree(
                root,
                now=time.time(),
                max_age_sec=3600,
                delete_proxy=True,
                failed_ids={"bad"},
            )
            self.assertFalse(temp.exists())
            self.assertFalse(proxy.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(raw.exists())
            self.assertFalse(failed_proxy.exists())
            self.assertTrue(failed_raw.exists())
            self.assertGreaterEqual(stats["bytes_reclaimed"], 3)
            self.assertEqual(stats["failed_projects_trimmed"], 1)

    def test_disk_guard(self) -> None:
        with patch("app.pipeline.disk.shutil.disk_usage", return_value=SimpleNamespace(free=10)):
            with self.assertRaises(AppError) as caught:
                ensure_free_disk(0)
        self.assertEqual(caught.exception.code, "DISK_FULL")


class WatermarkTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("WATERMARK_TEXT", None)
        os.environ.pop("WATERMARK_IMAGE", None)
        os.environ.pop("FONTS_DIR", None)
        get_settings.cache_clear()

    def test_off_by_default_keeps_export_hash(self) -> None:
        os.environ["WATERMARK_TEXT"] = ""
        os.environ["WATERMARK_IMAGE"] = ""
        get_settings.cache_clear()
        self.assertEqual(watermark_token(), "")
        self.assertEqual(_hash(), _hash(watermark=""))
        self.assertNotEqual(_hash(), _hash(watermark="text:Cliping"))

    def test_drawtext_when_text_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "DejaVuSans.ttf").write_bytes(b"font")
            os.environ["FONTS_DIR"] = tmp
            os.environ["WATERMARK_TEXT"] = "Cliping"
            os.environ["WATERMARK_IMAGE"] = ""
            get_settings.cache_clear()
            filt = text_filter()
            self.assertIn("drawtext=", filt)
            self.assertIn("Cliping", filt)
            self.assertEqual(watermark_token(), "text:Cliping")


class RedactTests(unittest.TestCase):
    def test_urls_are_removed(self) -> None:
        text = scrub_text("download failed https://example.com/watch?token=secret")
        self.assertNotIn("secret", text)
        self.assertIn("[url]", text)
