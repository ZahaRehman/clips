import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.config import get_settings
from app.errors import AppError
from app.pipeline.align import apply_aligner
from app.pipeline.captions import (
    caption_draw_text,
    caption_font_name,
    SAFE_TOP,
    caption_pages,
    resolve_caption_words,
    safe_margins,
    write_ass,
)
from app.pipeline.export import export_input_hash, should_skip_encode
from app.pipeline.framing import crop_for_framing, framing_of, parse_framing
from app.pipeline.reframe import box_from_center, face_crop, ffmpeg_crop_filter, median_face_center, static_crop


WORDS = [
    {"word": "Helo", "start": 1.0, "end": 1.2},
    {"word": "world", "start": 1.2, "end": 1.5},
    {"word": "this", "start": 1.6, "end": 1.8},
    {"word": "is", "start": 1.8, "end": 2.0},
    {"word": "Cliping", "start": 2.0, "end": 2.4},
]


class CropTests(unittest.TestCase):
    def test_three_face_positions_stay_in_frame(self) -> None:
        center = static_crop("center", {}, 1920, 1080, 1080, 1920, 0, 10)
        for norm_x in (0.18, 0.50, 0.82):
            box = box_from_center(1920, 1080, 1080, 1920, 1920 * norm_x, 540, "face", anchor_y=0.40)
            self.assertLessEqual(box["x"], norm_x)
            self.assertLessEqual(norm_x, box["x"] + box["w"])
            self.assertGreaterEqual(box["x"], 0)
            self.assertLessEqual(box["x"] + box["w"], 1.001)
        left_face = 0.18
        self.assertFalse(center["x"] <= left_face <= center["x"] + center["w"])

    def test_left_right_center_differ(self) -> None:
        left = static_crop("left", {}, 1920, 1080, 1080, 1920, 0, 10)
        center = static_crop("center", {}, 1920, 1080, 1080, 1920, 0, 10)
        right = static_crop("right", {}, 1920, 1080, 1080, 1920, 0, 10)
        self.assertLess(left["x"], center["x"])
        self.assertGreater(right["x"], center["x"])
        self.assertEqual(left["mode"], "left")
        self.assertNotEqual(ffmpeg_crop_filter(left, 1920, 1080, 1080, 1920), ffmpeg_crop_filter(center, 1920, 1080, 1080, 1920))

    def test_no_faces_fall_back_to_center(self) -> None:
        self.assertIsNone(median_face_center([[], []]))
        fallback = face_crop(None, 1920, 1080, 1080, 1920, 1, 9)
        center = static_crop("center", {}, 1920, 1080, 1080, 1920, 1, 9)
        self.assertEqual(fallback["fallback"], "center")
        self.assertAlmostEqual(fallback["x"], center["x"])
        self.assertEqual(fallback["mode"], "face")

    def test_largest_face_then_median(self) -> None:
        center = median_face_center(
            [
                [{"cx": 0.2, "cy": 0.4, "area": 10}, {"cx": 0.8, "cy": 0.4, "area": 50}],
                [{"cx": 0.3, "cy": 0.6, "area": 40}, {"cx": 0.9, "cy": 0.6, "area": 5}],
            ]
        )
        self.assertIsNotNone(center)
        assert center is not None
        self.assertAlmostEqual(center[0], 0.55)
        self.assertAlmostEqual(center[1], 0.5)

    def test_custom_box_shifts_left(self) -> None:
        custom = static_crop(
            "custom",
            {"x": 0.0, "y": 0.0, "w": 0.3, "h": 1.0},
            1920,
            1080,
            1080,
            1920,
            0,
            5,
        )
        center = static_crop("center", {}, 1920, 1080, 1080, 1920, 0, 5)
        self.assertLess(custom["x"], center["x"])
        self.assertEqual(custom["mode"], "custom")


class CaptionTests(unittest.TestCase):
    def test_karaoke_pages_and_safe_zone(self) -> None:
        self.assertEqual([len(page) for page in caption_pages(WORDS)], [4, 1])
        margin_l, margin_r, margin_v = safe_margins(1080, 1920)
        self.assertEqual((margin_l, margin_r, margin_v), (80, 200, 420))
        text_top = 1920 - margin_v - 72
        self.assertGreater(text_top, SAFE_TOP)

        path = Path(tempfile.mkdtemp()) / "captions.ass"
        write_ass(path, WORDS, 1.0)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("Dialogue:"), 2)
        self.assertIn(r"{\k", text)
        self.assertIn("DejaVu Sans", text)
        self.assertNotIn("Noto Sans Devanagari", text)
        self.assertIn(",80,200,420,1", text)
        self.assertNotIn("Helo world this is Cliping", text)

    def test_hindi_caption_keeps_devanagari_and_drops_other_letters(self) -> None:
        self.assertEqual(caption_draw_text("यह 我 ok"), "यह  ok")
        self.assertEqual(caption_font_name("यह"), "Noto Sans Devanagari")
        path = Path(tempfile.mkdtemp()) / "captions.ass"
        write_ass(
            path,
            [{"word": "यह", "start": 1.0, "end": 1.4}, {"word": "我", "start": 1.4, "end": 1.8}],
            0.0,
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("Noto Sans Devanagari", text)
        self.assertIn("यह", text)
        self.assertNotIn("我", text)

    def test_plain_override_keeps_timing_when_counts_match(self) -> None:
        fixed = resolve_caption_words(WORDS, 0.5, 3.0, "Hello world this is Cliping")
        self.assertEqual(fixed[0]["word"], "Hello")
        self.assertEqual(fixed[0]["start"], 1.0)
        self.assertEqual(fixed[-1]["word"], "Cliping")

    def test_json_override_replaces_words(self) -> None:
        raw = '{"words":[{"word":"fixed","start":1.1,"end":1.4}]}'
        fixed = resolve_caption_words(WORDS, 0, 3, raw)
        self.assertEqual(fixed, [{"word": "fixed", "start": 1.1, "end": 1.4}])


class ExportHashTests(unittest.TestCase):
    def test_hash_changes_with_trim_crop_and_captions(self) -> None:
        crop = {"mode": "center", "x": 0.2, "y": 0.0, "w": 0.56, "h": 1.0}
        base = dict(t_start=1.0, t_end=5.0, crop=crop, words=WORDS, preset="shorts_1080", width=1080, height=1920)
        original = export_input_hash(**base)
        self.assertNotEqual(original, export_input_hash(**{**base, "t_end": 6.0}))
        self.assertNotEqual(original, export_input_hash(**{**base, "crop": {**crop, "x": 0.0}}))
        edited = [{**WORDS[0], "word": "Hello"}, *WORDS[1:]]
        self.assertNotEqual(original, export_input_hash(**{**base, "words": edited}))
        self.assertEqual(original, export_input_hash(**base))
        self.assertTrue(should_skip_encode(False, True, original, original))
        self.assertFalse(should_skip_encode(True, True, original, original))
        self.assertFalse(should_skip_encode(False, False, original, original))

    def test_ffmpeg_center_export_is_vertical(self) -> None:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg missing")
        tmp = Path(tempfile.mkdtemp())
        source = tmp / "source.mp4"
        ass = tmp / "captions.ass"
        out = tmp / "export.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=1280x720:d=1.2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1.2",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(source),
            ],
            check=True,
            capture_output=True,
        )
        write_ass(ass, [{"word": "Hello", "start": 0.1, "end": 0.8}], 0.0)
        box = static_crop("center", {}, 1280, 720, 1080, 1920, 0, 1.2)
        fonts = Path(__file__).resolve().parents[1] / "app" / "assets" / "fonts"
        vf = f"{ffmpeg_crop_filter(box, 1280, 720, 1080, 1920)},ass={ass}:fontsdir={fonts}"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vf",
                vf,
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(out)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn('"width": 1080', probe.stdout)
        self.assertIn('"height": 1920', probe.stdout)
        self.assertIn('"codec_name": "aac"', probe.stdout)
        self.assertIn('"codec_name": "h264"', probe.stdout)


class AlignerTests(unittest.TestCase):
    def test_default_aligner_is_passthrough(self) -> None:
        previous = os.environ.get("ASR_ALIGNER")
        os.environ["ASR_ALIGNER"] = "none"
        get_settings.cache_clear()
        try:
            words = [{"word": "hello", "start": 0.0, "end": 0.2}]
            self.assertIs(apply_aligner(words, [], Path("unused.wav"), "en"), words)
        finally:
            if previous is None:
                os.environ.pop("ASR_ALIGNER", None)
            else:
                os.environ["ASR_ALIGNER"] = previous
            get_settings.cache_clear()

    def test_whisperx_without_package_is_explicit(self) -> None:
        previous = os.environ.get("ASR_ALIGNER")
        os.environ["ASR_ALIGNER"] = "whisperx"
        get_settings.cache_clear()
        try:
            try:
                import whisperx  # noqa: F401
            except ImportError:
                with self.assertRaises(AppError) as caught:
                    apply_aligner([], [], Path("unused.wav"), "en")
                self.assertIn("not installed", caught.exception.message)
            else:
                self.skipTest("whisperx is installed")
        finally:
            if previous is None:
                os.environ.pop("ASR_ALIGNER", None)
            else:
                os.environ["ASR_ALIGNER"] = previous
            get_settings.cache_clear()


class FramingTests(unittest.TestCase):
    def test_gameplay_uses_a_center_window(self) -> None:
        self.assertEqual(crop_for_framing("gameplay"), {"mode": "center"})
        self.assertEqual(crop_for_framing("face"), {"mode": "face"})

    def test_missing_framing_stays_on_the_face(self) -> None:
        self.assertEqual(framing_of({}), "face")
        self.assertEqual(framing_of({"framing": "gameplay"}), "gameplay")
        self.assertEqual(parse_framing(None), "face")

    def test_unknown_framing_is_rejected(self) -> None:
        with self.assertRaises(AppError) as caught:
            parse_framing("zoom")
        self.assertEqual(caught.exception.code, "INVALID_FRAMING")


if __name__ == "__main__":
    unittest.main()
