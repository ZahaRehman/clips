import unittest
from datetime import datetime, timedelta, timezone

from app.errors import AppError
from app.youtube.errors import classify_api_error, map_processing, stops_batch
from app.youtube.schedule import (
    ClipCandidate,
    insert_body,
    next_upload_action,
    plan_batch,
    schedule_times,
    snapshot_text,
)


def _now() -> datetime:
    return datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def _clip(clip_id: str = "c1", **overrides) -> ClipCandidate:
    data = dict(
        clip_id=clip_id,
        export_id="e1",
        export_hash="abc",
        export_status="ready",
        duration_sec=35.0,
        post_title="A clear title",
        post_description="What the clip says.\n\n#one #two",
    )
    data.update(overrides)
    return ClipCandidate(**data)


class ScheduleTests(unittest.TestCase):
    def test_spacing(self) -> None:
        start = _now()
        times = schedule_times(start, 180, 3, now=_now())
        self.assertEqual(times, [start, start + timedelta(hours=3), start + timedelta(hours=6)])

    def test_rejects_more_than_a_year(self) -> None:
        with self.assertRaises(AppError) as caught:
            schedule_times(_now() + timedelta(days=400), 180, 2, now=_now())
        self.assertEqual(caught.exception.code, "INVALID_SCHEDULE")

    def test_past_slot_posts_immediately_later_slots_are_private(self) -> None:
        plan = plan_batch(
            [_clip("c1"), _clip("c2", export_id="e2", export_hash="def")],
            privacy="public",
            start_at=_now() - timedelta(minutes=5),
            every_minutes=180,
            made_for_kids=False,
            rights_confirmed=True,
            now=_now(),
        )
        self.assertIsNone(plan.rows[0].publish_at)
        self.assertEqual(plan.rows[0].privacy_status, "public")
        self.assertEqual(plan.rows[1].privacy_status, "private")
        self.assertEqual(plan.rows[1].publish_at, _now() - timedelta(minutes=5) + timedelta(hours=3))

    def test_second_plan_reuses_the_active_row(self) -> None:
        first = plan_batch(
            [_clip()],
            privacy="private",
            start_at=None,
            every_minutes=None,
            made_for_kids=False,
            rights_confirmed=True,
            now=_now(),
        )
        self.assertEqual(first.rows[0].action, "create")
        second = plan_batch(
            [_clip(active_publish_id="p1")],
            privacy="private",
            start_at=None,
            every_minutes=None,
            made_for_kids=False,
            rights_confirmed=True,
            now=_now(),
        )
        self.assertEqual(second.rows[0].action, "reuse")
        self.assertEqual(second.rows[0].existing_id, "p1")
        self.assertTrue(any("already posted" in item for item in second.warnings))


class SnapshotTests(unittest.TestCase):
    def test_sent_text_matches_the_snapshot(self) -> None:
        title, description = snapshot_text("  Kept   title ", "Line one.\n\n#kept")
        body = insert_body(
            title=title,
            description=description,
            category_id="22",
            privacy_status="private",
            publish_at=None,
            made_for_kids=False,
            contains_synthetic_media=False,
            language="en",
        )
        self.assertEqual(body["snippet"]["defaultLanguage"], "en")
        self.assertEqual(body["snippet"]["title"], title)
        self.assertEqual(body["snippet"]["description"], description)
        self.assertNotIn("hashtag", body["snippet"]["description"].lower())
        self.assertEqual(body["snippet"]["description"], "Line one.\n\n#kept")
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertNotIn("publishAt", body["status"])

    def test_scheduled_body_stays_private(self) -> None:
        when = _now() + timedelta(hours=3)
        body = insert_body(
            title="Title",
            description="Body",
            category_id="22",
            privacy_status="public",
            publish_at=when,
            made_for_kids=True,
            contains_synthetic_media=False,
            language=None,
        )
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["status"]["publishAt"], "2026-11-15T15:00:00.000Z")
        self.assertTrue(body["status"]["selfDeclaredMadeForKids"])
        self.assertFalse(body["status"]["containsSyntheticMedia"])

    def test_validation_gates(self) -> None:
        kwargs = dict(privacy="private", start_at=None, every_minutes=None, made_for_kids=False, now=_now())
        with self.assertRaises(AppError) as rights:
            plan_batch([_clip()], rights_confirmed=False, **kwargs)
        self.assertEqual(rights.exception.code, "RIGHTS_REQUIRED")
        with self.assertRaises(AppError) as kids:
            plan_batch([_clip()], rights_confirmed=True, made_for_kids=None, privacy="private", start_at=None, every_minutes=None, now=_now())
        self.assertEqual(kids.exception.code, "MADE_FOR_KIDS_REQUIRED")
        with self.assertRaises(AppError) as too_long:
            plan_batch([_clip(duration_sec=181)], rights_confirmed=True, **kwargs)
        self.assertEqual(too_long.exception.code, "EXPORT_TOO_LONG")
        warned = plan_batch([_clip(duration_sec=75)], rights_confirmed=True, **kwargs)
        self.assertTrue(any("over 60" in item for item in warned.warnings))


class ErrorClassTests(unittest.TestCase):
    def test_quota_stops_the_batch_and_copyright_does_not(self) -> None:
        self.assertEqual(classify_api_error(403, ["quotaExceeded"]), "quota_stop")
        self.assertEqual(classify_api_error(400, ["uploadLimitExceeded"]), "quota_stop")
        self.assertTrue(stops_batch("quotaExceeded"))
        self.assertTrue(stops_batch("uploadLimitExceeded"))
        self.assertTrue(stops_batch("YOUTUBE_DISCONNECTED"))
        self.assertEqual(classify_api_error(400, ["copyright"]), "no_retry")
        self.assertEqual(classify_api_error(400, ["duplicate"]), "no_retry")
        self.assertFalse(stops_batch("copyright"))
        self.assertFalse(stops_batch("duplicate"))
        self.assertEqual(classify_api_error(503, []), "retry")
        self.assertEqual(classify_api_error(None, []), "retry")

    def test_processing_map(self) -> None:
        future = _now() + timedelta(hours=1)
        self.assertEqual(
            map_processing(
                upload_status="processed",
                processing_status="succeeded",
                failure_reason=None,
                rejection_reason=None,
                processing_failure_reason=None,
                publish_at=future,
                now=_now(),
            ),
            ("scheduled", None),
        )
        self.assertEqual(
            map_processing(
                upload_status="processed",
                processing_status="succeeded",
                failure_reason=None,
                rejection_reason=None,
                processing_failure_reason=None,
                publish_at=None,
                now=_now(),
            ),
            ("live", None),
        )
        self.assertEqual(
            map_processing(
                upload_status="rejected",
                processing_status="failed",
                failure_reason=None,
                rejection_reason="copyright",
                processing_failure_reason=None,
                publish_at=None,
                now=_now(),
            ),
            ("failed", "copyright"),
        )

    def test_resume_does_not_start_a_second_insert(self) -> None:
        self.assertEqual(next_upload_action("vid", "https://upload.example/session"), "poll")
        self.assertEqual(next_upload_action(None, "https://upload.example/session"), "resume")
        self.assertEqual(next_upload_action(None, None), "start")


if __name__ == "__main__":
    unittest.main()
