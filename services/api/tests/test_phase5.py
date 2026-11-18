import os
import unittest
from unittest.mock import patch

import httpx

from app.config import get_settings
from app.errors import AppError
from app.llm.gemini import GeminiError, classify_quota, generate_json
from app.pipeline.clip_plan import plan_reason, planned_clip_count, speech_seconds
from app.pipeline.post_text import (
    DESCRIPTION_MAX_BYTES,
    check_composed,
    check_hashtags,
    check_title,
    compose_description,
    description_body,
    fit_description,
    normalize_hashtags,
    sanitize_title,
)
from app.pipeline.render_clips import render_progress
from app.pipeline.score import _pick
from app.pipeline.windows import _note_kept_shortfall, cap_window, keep_non_overlapping


def _env(**values: str):
    return patch.dict(os.environ, values, clear=False)


def _quota_body(quota_id: str, delay: str = "12s") -> dict:
    return {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
            ],
        }
    }


def _ok_body(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


class ClipPlanTests(unittest.TestCase):
    def test_two_ten_and_sixty_minutes(self) -> None:
        self.assertEqual(planned_clip_count(10, 120, 35), 3)
        self.assertEqual(planned_clip_count(10, 600, 35), 10)
        self.assertEqual(planned_clip_count(10, 3600, 35), 10)
        self.assertEqual(planned_clip_count(30, 600, 35), 17)

    def test_short_sources(self) -> None:
        self.assertEqual(planned_clip_count(10, 25, 35, min_seconds=20), 1)
        self.assertEqual(planned_clip_count(10, 10, 35, min_seconds=20), 0)
        self.assertEqual(planned_clip_count(0, 600, 35), 0)

    def test_reason_only_when_short(self) -> None:
        self.assertEqual(plan_reason(10, 10, 600), "")
        self.assertIn("enough for 3 of the 10", plan_reason(10, 3, 120))

    def test_speech_seconds_skips_long_silence(self) -> None:
        words = [
            {"word": "a", "start": 0.0, "end": 1.0},
            {"word": "b", "start": 1.5, "end": 2.0},
            {"word": "c", "start": 10.0, "end": 11.0},
        ]
        self.assertAlmostEqual(speech_seconds(words), 3.0)


class PostTextTests(unittest.TestCase):
    def test_title_rules(self) -> None:
        self.assertEqual(check_title("  Big   idea "), "Big idea")
        for bad in ("", "a <b>", "x" * 101):
            with self.assertRaises(AppError) as caught:
                check_title(bad)
            self.assertEqual(caught.exception.code, "INVALID_POST_TITLE")

    def test_model_title_is_cleaned_not_rejected(self) -> None:
        long = "word " * 40
        cleaned = sanitize_title(long, "fallback")
        self.assertLessEqual(len(cleaned), 100)
        self.assertTrue(cleaned.endswith("…"))
        self.assertEqual(sanitize_title("<>", "Heuristic title"), "Heuristic title")

    def test_hashtags_normalized_and_deduped(self) -> None:
        tags = normalize_hashtags(["#AI", "ai", "machine learning", "##tips!", "", "c++", "Ünïcode"])
        self.assertEqual(tags, ["#AI", "#machinelearning", "#tips", "#c", "#Ünïcode"])
        with self.assertRaises(AppError):
            check_hashtags(["a", "b", "c", "d", "e", "f"])

    def test_compose_and_body_round_trip(self) -> None:
        composed = compose_description("Line <one>.", ["#a", "#b"])
        self.assertEqual(composed, "Line one.\n\n#a #b")
        self.assertEqual(description_body(composed, ["#a", "#b"]), "Line one.")
        self.assertEqual(compose_description("", ["#a"]), "#a")

    def test_description_byte_limit(self) -> None:
        huge = "é" * 4000
        with self.assertRaises(AppError):
            check_composed(compose_description(huge, ["#a"]))
        fitted = fit_description(huge, ["#a", "#b", "#c"])
        self.assertLess(len(fitted.encode("utf-8")), DESCRIPTION_MAX_BYTES)
        self.assertTrue(fitted.endswith("#a #b #c"))


class OverlapPlanTests(unittest.TestCase):
    def test_autopilot_overlap_keeps_a_later_window(self) -> None:
        raw = [
            {"start_sec": 0, "end_sec": 35, "score": 90, "title": "a", "reason": "a"},
            {"start_sec": 32, "end_sec": 67, "score": 80, "title": "b", "reason": "b"},
            {"start_sec": 70, "end_sec": 105, "score": 70, "title": "c", "reason": "c"},
        ]
        tight = _pick(raw, 3, max_overlap=0.0)
        loose = _pick(raw, 3, max_overlap=5.0)
        self.assertEqual([clip["start_sec"] for clip in tight], [0, 70])
        self.assertEqual(len(loose), 3)

    def test_shortfall_reason_names_the_kept_count(self) -> None:
        checkpoint: dict = {"segment_score": {"plan": {"planned": 2}}}
        _note_kept_shortfall(
            checkpoint,
            {"plan": {"requested": 10, "planned": 2, "speech_seconds": 94.7, "reason": ""}},
            1,
        )
        reason = checkpoint["segment_score"]["plan"]["reason"]
        self.assertIn("enough for 2 of the 10", reason)
        self.assertIn("Only 1 fit", reason)

    def test_render_progress_stays_above_propose(self) -> None:
        self.assertAlmostEqual(render_progress(0), 0.96)
        self.assertGreater(render_progress(0.5), 0.96)
        self.assertAlmostEqual(render_progress(1), 0.99)
        self.assertLess(render_progress(1), 1)


class WindowFitTests(unittest.TestCase):
    def test_cap_window_ends_on_a_word(self) -> None:
        words = [{"word": f"w{i}", "start": float(i), "end": i + 0.8} for i in range(80)]
        start, end = cap_window(0.0, 64.0, words, 59.0)
        self.assertLessEqual(end - start, 59.0)
        self.assertGreaterEqual(end, 57.8)

    def test_keep_non_overlapping_respects_taken(self) -> None:
        proposals = [
            {"t_start": 0, "t_end": 30, "score": 90},
            {"t_start": 20, "t_end": 50, "score": 80},
            {"t_start": 60, "t_end": 90, "score": 70},
            {"t_start": 100, "t_end": 130, "score": 60},
        ]
        kept = keep_non_overlapping(proposals, keep=2, taken=[(95, 125)])
        self.assertEqual([p["t_start"] for p in kept], [0, 60])


class GeminiTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_classify_day_vs_minute(self) -> None:
        code, wait, qid = classify_quota(_quota_body("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
        self.assertEqual((code, qid), ("GEMINI_QUOTA_DAY", "GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
        code, wait, _ = classify_quota(_quota_body("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "7s"))
        self.assertEqual((code, wait), ("GEMINI_QUOTA_MINUTE", 7.0))
        self.assertEqual(classify_quota(None)[0], "GEMINI_QUOTA_MINUTE")

    def test_no_key(self) -> None:
        with _env(GEMINI_API_KEY=""):
            get_settings.cache_clear()
            with self.assertRaises(GeminiError) as caught:
                generate_json("hi", {"type": "object"})
        self.assertEqual(caught.exception.code, "GEMINI_NO_KEY")

    def test_bad_key_maps_to_http(self) -> None:
        response = httpx.Response(400, json={"error": {"message": "API key not valid."}})
        with _env(GEMINI_API_KEY="bad"), patch("app.llm.gemini.httpx.post", return_value=response):
            get_settings.cache_clear()
            with self.assertRaises(GeminiError) as caught:
                generate_json("hi", {"type": "object"})
        self.assertEqual(caught.exception.code, "GEMINI_HTTP")

    def test_daily_quota_tries_fallback_model_once(self) -> None:
        calls: list[str] = []

        def fake_post(url: str, **_kwargs):
            calls.append(url)
            if "primary-model" in url:
                return httpx.Response(429, json=_quota_body("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
            return httpx.Response(200, json=_ok_body('{"ok": true}'))

        with _env(GEMINI_API_KEY="k", GEMINI_MODEL="primary-model", GEMINI_FALLBACK_MODEL="lite-model"):
            get_settings.cache_clear()
            with patch("app.llm.gemini.httpx.post", side_effect=fake_post):
                self.assertEqual(generate_json("hi", {"type": "object"}), {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertIn("lite-model", calls[1])

    def test_minute_quota_retries_once_then_raises(self) -> None:
        response = httpx.Response(429, json=_quota_body("GenerateRequestsPerMinutePerProjectPerModel", "0s"))
        with _env(GEMINI_API_KEY="k", GEMINI_FALLBACK_MODEL=""):
            get_settings.cache_clear()
            with patch("app.llm.gemini.httpx.post", return_value=response) as post, patch("app.llm.gemini.time.sleep"):
                with self.assertRaises(GeminiError) as caught:
                    generate_json("hi", {"type": "object"})
        self.assertEqual(caught.exception.code, "GEMINI_QUOTA_MINUTE")
        self.assertEqual(post.call_count, 2)

    def test_non_json_output_is_bad_response(self) -> None:
        response = httpx.Response(200, json=_ok_body("not json"))
        with _env(GEMINI_API_KEY="k"), patch("app.llm.gemini.httpx.post", return_value=response):
            get_settings.cache_clear()
            with self.assertRaises(GeminiError) as caught:
                generate_json("hi", {"type": "object"})
        self.assertEqual(caught.exception.code, "GEMINI_BAD_RESPONSE")

    def test_ranking_without_key_reports_code(self) -> None:
        from app.pipeline.llm_rank import rank_with_llm

        with _env(LLM_PROVIDER="gemini", GEMINI_API_KEY=""):
            get_settings.cache_clear()
            clips, error = rank_with_llm(
                [{"start": 0, "end": 30, "text": "Why this mistake is a lesson."}],
                min_sec=20,
                max_sec=59,
                target_count=4,
                media_duration=120,
            )
        self.assertIsNone(clips)
        self.assertEqual(error, "GEMINI_NO_KEY")


if __name__ == "__main__":
    unittest.main()
