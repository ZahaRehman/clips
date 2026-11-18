import unittest
from datetime import datetime, timedelta, timezone

from app.errors import AppError
from app.tiktok.errors import classify_tiktok
from app.tiktok.oauth import pkce_challenge
from app.tiktok.transfer import _range_end
from app.tiktok.plan import (
    CHUNK_OVER_64,
    ClipPost,
    CreatorRules,
    chunk_plan,
    next_publish_step,
    plan_posts,
    post_info_title,
    put_spans,
    snapshot_caption,
    utf16_len,
)


def _rules() -> CreatorRules:
    return CreatorRules(
        privacy_level_options=["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
        comment_disabled=False,
        duet_disabled=False,
        stitch_disabled=True,
        max_video_post_duration_sec=180,
    )


def _clip(**overrides) -> ClipPost:
    data = dict(
        clip_id="c1",
        export_id="e1",
        export_hash="abc",
        export_status="ready",
        duration_sec=35.0,
        frame_rate=30.0,
        post_title="A clear title",
        post_description="What the clip says.\n\n#one",
    )
    data.update(overrides)
    return ClipPost(**data)


class ChunkTests(unittest.TestCase):
    def test_under_5mb_is_one_chunk_the_size_of_the_file(self) -> None:
        size = 4 * 1024 * 1024
        plan = chunk_plan(size)
        self.assertEqual(plan.chunk_size, size)
        self.assertEqual(plan.total_chunk_count, 1)
        self.assertEqual(put_spans(size, plan), [(0, size - 1)])

    def test_20mb_is_one_chunk(self) -> None:
        size = 20 * 1024 * 1024
        plan = chunk_plan(size)
        self.assertEqual(plan.chunk_size, size)
        self.assertEqual(plan.total_chunk_count, 1)

    def test_just_over_64mb_uses_10mb_chunks_and_absorbs_the_tail(self) -> None:
        size = 64 * 1024 * 1024 + 1
        plan = chunk_plan(size)
        self.assertEqual(plan.chunk_size, CHUNK_OVER_64)
        self.assertEqual(plan.total_chunk_count, size // CHUNK_OVER_64)
        spans = put_spans(size, plan)
        self.assertEqual(len(spans), plan.total_chunk_count)
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], size - 1)
        last = spans[-1][1] - spans[-1][0] + 1
        self.assertGreater(last, CHUNK_OVER_64)
        self.assertLess(last, CHUNK_OVER_64 + CHUNK_OVER_64)

    def test_tiktok_example_last_body_is_the_10mb_chunk_plus_leftover(self) -> None:
        size = 50_000_123
        plan = chunk_plan(size)
        self.assertEqual((plan.chunk_size, plan.total_chunk_count), (size, 1))
        spans = put_spans(size, type(plan)(CHUNK_OVER_64, 5))
        self.assertEqual(len(spans), 5)
        self.assertEqual(spans[-1][1] - spans[-1][0] + 1, 10_000_123)


class CaptionTests(unittest.TestCase):
    def test_utf16_counts_emoji_as_two_units(self) -> None:
        self.assertEqual(len("🙂"), 1)
        self.assertEqual(utf16_len("🙂"), 2)
        self.assertGreater(utf16_len("🙂" * 1101), 2200)

    def test_sent_caption_equals_the_snapshot(self) -> None:
        caption = snapshot_caption("A clear title", "What the clip says.\n\n#one")
        self.assertEqual(caption, "A clear title\nWhat the clip says.\n\n#one")
        self.assertEqual(post_info_title(caption), caption)


class PlanTests(unittest.TestCase):
    def test_privacy_outside_the_list_is_rejected(self) -> None:
        with self.assertRaises(AppError) as caught:
            plan_posts(
                [_clip()],
                privacy_level="FOLLOWER_OF_CREATOR",
                allow_comment=False,
                allow_duet=False,
                allow_stitch=False,
                brand_content=False,
                rules=_rules(),
                watermark_on=False,
                rights_confirmed=True,
                music_confirmed=True,
            )
        self.assertEqual(caught.exception.code, "privacy_level_option_mismatch")

    def test_branded_content_with_only_me_is_rejected(self) -> None:
        with self.assertRaises(AppError) as caught:
            plan_posts(
                [_clip()],
                privacy_level="SELF_ONLY",
                allow_comment=False,
                allow_duet=False,
                allow_stitch=False,
                brand_content=True,
                rules=_rules(),
                watermark_on=False,
                rights_confirmed=True,
                music_confirmed=True,
            )
        self.assertEqual(caught.exception.code, "BRAND_CONTENT_SELF_ONLY")

    def test_frame_rate_and_watermark_are_refused_before_any_row(self) -> None:
        with self.assertRaises(AppError) as rate:
            plan_posts(
                [_clip(frame_rate=15)],
                privacy_level="SELF_ONLY",
                allow_comment=False,
                allow_duet=False,
                allow_stitch=False,
                brand_content=False,
                rules=_rules(),
                watermark_on=False,
                rights_confirmed=True,
                music_confirmed=True,
            )
        self.assertEqual(rate.exception.code, "FRAME_RATE")
        with self.assertRaises(AppError) as mark:
            plan_posts(
                [_clip()],
                privacy_level="SELF_ONLY",
                allow_comment=False,
                allow_duet=False,
                allow_stitch=False,
                brand_content=False,
                rules=_rules(),
                watermark_on=True,
                rights_confirmed=True,
                music_confirmed=True,
            )
        self.assertEqual(mark.exception.code, "WATERMARK_PRESENT")

    def test_existing_publish_id_is_not_a_new_init(self) -> None:
        now = datetime(2026, 11, 17, tzinfo=timezone.utc)
        self.assertEqual(
            next_publish_step(publish_id="pub_1", status="uploading", upload_started_at=now, now=now),
            "resume",
        )
        self.assertEqual(
            next_publish_step(publish_id="pub_1", status="processing", upload_started_at=now, now=now),
            "poll",
        )
        self.assertEqual(
            next_publish_step(
                publish_id="pub_1",
                status="uploading",
                upload_started_at=now - timedelta(hours=2),
                now=now,
            ),
            "expired",
        )
        self.assertNotEqual(
            next_publish_step(publish_id="pub_1", status="processing", upload_started_at=now, now=now),
            "init",
        )
        planned = plan_posts(
            [_clip(active_publish_id="row-1")],
            privacy_level="SELF_ONLY",
            allow_comment=False,
            allow_duet=False,
            allow_stitch=False,
            brand_content=False,
            rules=_rules(),
            watermark_on=False,
            rights_confirmed=True,
            music_confirmed=True,
        )
        self.assertEqual(planned[0].action, "reuse")
        self.assertEqual(planned[0].existing_id, "row-1")


class ClassifyTests(unittest.TestCase):
    def test_batch_stop_versus_row_fail(self) -> None:
        self.assertEqual(classify_tiktok("spam_risk_too_many_posts"), "stop_batch")
        self.assertEqual(classify_tiktok("unaudited_client_can_only_post_to_private_accounts"), "stop_batch")
        self.assertEqual(classify_tiktok("file_format_check_failed"), "fail_row")
        self.assertEqual(classify_tiktok("spam_risk_text"), "fail_row")
        self.assertEqual(classify_tiktok("rate_limit_exceeded"), "retry")
        self.assertEqual(classify_tiktok("access_token_invalid"), "disconnected")


class RangeHeaderTests(unittest.TestCase):
    def test_content_range_end_is_the_next_offset(self) -> None:
        self.assertEqual(_range_end("bytes 0-999/5000"), 1000)
        self.assertIsNone(_range_end("not-a-range"))
        self.assertIsNone(_range_end(None))


class PkceTests(unittest.TestCase):
    def test_challenge_is_hex_sha256(self) -> None:
        self.assertEqual(
            pkce_challenge("test"),
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        )


if __name__ == "__main__":
    unittest.main()
