"""Whole-screen (16:9) framing: no crop, landscape export, landscape captions."""

import tempfile
import unittest
from pathlib import Path

from app.errors import AppError
from app.pipeline.captions import safe_margins, write_ass
from app.pipeline.export import export_size
from app.pipeline.framing import crop_for_framing, parse_framing
from app.pipeline.reframe import ffmpeg_crop_filter, static_crop


class FullFrameTests(unittest.TestCase):
    def test_framing_full_maps_to_full_crop(self) -> None:
        self.assertEqual(parse_framing("full"), "full")
        self.assertEqual(crop_for_framing("full"), {"mode": "full"})
        with self.assertRaises(AppError):
            parse_framing("square")

    def test_export_size_is_landscape_only_for_full(self) -> None:
        self.assertEqual(export_size("full"), (1920, 1080))
        self.assertEqual(export_size("face"), (1080, 1920))
        self.assertEqual(export_size("center"), (1080, 1920))

    def test_full_crop_keeps_whole_frame_without_stretching(self) -> None:
        box = static_crop("full", {}, 1920, 1080, 1920, 1080, 10.0, 40.0)
        self.assertEqual((box["x"], box["y"], box["w"], box["h"]), (0.0, 0.0, 1.0, 1.0))
        vf = ffmpeg_crop_filter(box, 1440, 1080, 1920, 1080)
        self.assertNotIn("crop=", vf)
        self.assertIn("force_original_aspect_ratio=decrease", vf)
        self.assertIn("pad=1920:1080", vf)

    def test_landscape_captions(self) -> None:
        self.assertEqual(safe_margins(1920, 1080), (120, 120, 80))
        path = Path(tempfile.mkdtemp()) / "c.ass"
        write_ass(path, [{"word": "hello", "start": 0.0, "end": 0.5}], 0.0, 1920, 1080)
        text = path.read_text()
        self.assertIn("PlayResX: 1920", text)
        self.assertIn("Style: Default,", text)
        self.assertIn(",56,", text)


if __name__ == "__main__":
    unittest.main()
