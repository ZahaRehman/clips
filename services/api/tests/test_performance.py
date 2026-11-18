"""Encode-speed settings, background proxy, clip counts per window, and ffmpeg error text."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import get_settings
from app.errors import AppError
from app.pipeline import export as export_mod
from app.pipeline import normalize
from app.pipeline.clip_plan import window_clip_request
from app.pipeline.ffmpeg_util import failure_message, x264_args

FONTS = Path(__file__).resolve().parents[1] / "app" / "assets" / "fonts"


class X264ArgsTests(unittest.TestCase):
    def test_preset_crf_and_fps_cap(self) -> None:
        args = x264_args("veryfast", 30, crf=21)
        self.assertEqual(args[:4], ["-c:v", "libx264", "-preset", "veryfast"])
        self.assertIn("-crf", args)
        self.assertEqual(args[args.index("-fpsmax") + 1], "30")
        self.assertEqual(args[-2:], ["-pix_fmt", "yuv420p"])

    def test_zero_fps_keeps_source_rate(self) -> None:
        self.assertNotIn("-fpsmax", x264_args("medium", 0))


class FailureMessageTests(unittest.TestCase):
    def test_signal_exit_is_named(self) -> None:
        text = "[Parsed_ass_2 @ 0x1] Error opening memory font 'DejaVu-LICENSE'\n"
        self.assertIn("killed (signal 9)", failure_message(-9, text))
        self.assertIn("killed (signal 9)", failure_message(137, text))

    def test_font_warning_is_not_the_cause(self) -> None:
        text = (
            "[Parsed_ass_2 @ 0x1] Error opening memory font 'DejaVu-LICENSE'\n"
            "[out#0/mp4 @ 0x2] Error opening output file /x/export.mp4.\n"
        )
        self.assertIn("Error opening output file", failure_message(1, text))

    def test_fonts_dir_holds_only_fonts(self) -> None:
        for path in FONTS.iterdir():
            self.assertIn(path.suffix.lower(), {".ttf", ".otf", ".ttc"}, path.name)


class EncodeArgvTests(unittest.TestCase):
    def test_seek_is_before_the_input(self) -> None:
        captured: list[list[str]] = []
        with patch.object(export_mod, "run_cmd", side_effect=lambda cmd: captured.append(cmd)):
            export_mod._encode_short(
                "ffmpeg",
                Path("/src.mp4"),
                {"streams": [{"codec_type": "audio"}]},
                1920,
                1080,
                1080,
                1920,
                {"mode": "center", "x": 0.34, "y": 0, "w": 0.32, "h": 1},
                Path("/c.ass"),
                "/fonts",
                240.0,
                35.0,
                None,
                "",
                Path("/out.mp4"),
            )
        cmd = captured[0]
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))
        self.assertEqual(cmd[cmd.index("-ss") + 1], "240.000")
        self.assertLess(cmd.index("-t"), cmd.index("-i"))
        self.assertEqual(cmd[cmd.index("-preset") + 1], get_settings().export_preset)
        self.assertIn("-fpsmax", cmd)


class WindowClipRequestTests(unittest.TestCase):
    def _project(self, **meta) -> SimpleNamespace:
        source_type = meta.pop("source_type", "url")
        return SimpleNamespace(source_meta=meta, source_type=source_type)

    def test_no_window_keeps_the_request(self) -> None:
        self.assertEqual(window_clip_request(self._project(), None, 10), 10)

    def test_chunked_video_splits_the_request_by_length(self) -> None:
        project = self._project(chunked=True, source_duration=3600)
        window = {"stream_start_sec": 0, "stream_end_sec": 300}
        self.assertEqual(window_clip_request(project, window, 10), 1)
        self.assertEqual(window_clip_request(project, window, 30), 3)

    def test_live_window_is_capped(self) -> None:
        project = self._project(source_type="live")
        window = {"stream_start_sec": 0, "stream_end_sec": 300}
        self.assertEqual(window_clip_request(project, window, 10), get_settings().live_window_max_clips)
        self.assertEqual(window_clip_request(project, window, 1), 1)


class BackgroundProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg missing")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["DATA_DIR"] = str(self.tmp)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        self.source = self.tmp / "source.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=1920x1080:r=60:d=1",
                "-f", "lavfi", "-i", "sine=duration=1", "-shortest",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(self.source),
            ],
            check=True,
            capture_output=True,
        )

    def test_proxy_finishes_in_the_background(self) -> None:
        final = self.tmp / "proxy" / "proxy.mp4"
        final.parent.mkdir()
        with patch.object(normalize, "_upsert_asset") as upsert:
            normalize._start_proxy("job-1", "p1", self.source, final, "abc")
            self.assertFalse(final.exists())
            normalize.finish_proxy(None, "job-1")
        self.assertTrue(final.is_file())
        self.assertFalse((final.parent / "proxy.partial.mp4").exists())
        upsert.assert_called_once()
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=height,r_frame_rate",
             "-of", "csv=p=0", str(final)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(probe.split(",")[0], "720")
        self.assertEqual(probe.split(",")[1], "30/1")

    def test_failed_proxy_raises_and_cleans_up(self) -> None:
        final = self.tmp / "proxy" / "proxy.mp4"
        final.parent.mkdir()
        normalize._start_proxy("job-2", "p1", self.tmp / "missing.mp4", final, "abc")
        with self.assertRaises(AppError):
            normalize.finish_proxy(None, "job-2")
        self.assertFalse((final.parent / "proxy.partial.mp4").exists())

    def test_abort_stops_and_forgets(self) -> None:
        final = self.tmp / "proxy" / "proxy.mp4"
        final.parent.mkdir()
        normalize._start_proxy("job-3", "p1", self.source, final, "abc")
        normalize.abort_proxy("job-3")
        normalize.finish_proxy(None, "job-3")
        self.assertFalse(final.exists())



class QueueSplitTests(unittest.TestCase):
    def test_uploads_and_exports_have_their_own_workers(self) -> None:
        from app import worker
        from app.worker_queues import PUBLISH_QUEUE, RENDER_QUEUE

        self.assertEqual(worker.PublishSettings.queue_name, PUBLISH_QUEUE)
        self.assertEqual(
            {f.__name__ for f in worker.PublishSettings.functions}, {"run_publish", "run_tiktok_publish"}
        )
        self.assertEqual(worker.RenderSettings.queue_name, RENDER_QUEUE)
        self.assertNotEqual(PUBLISH_QUEUE, RENDER_QUEUE)

if __name__ == "__main__":
    unittest.main()
