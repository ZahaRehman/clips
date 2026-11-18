"""Live discovery: parse a search page, drop ended broadcasts, cache, quota."""

from __future__ import annotations

import unittest

import httpx

from app.errors import AppError
from app.youtube.discover import (
    apply_video_details,
    clean_filters,
    clear_live_cache,
    load_discovery,
    merge_streams,
    public_ttl,
    quota_reason,
    rows_from_broadcasts,
    rows_from_search,
)


def _search_item(video_id: str, content: str = "live", title: str = "Show") -> dict:
    return {
        "id": {"kind": "youtube#video", "videoId": video_id},
        "snippet": {
            "title": title,
            "channelTitle": "Channel",
            "channelId": "UCxxxx",
            "liveBroadcastContent": content,
            "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/x/mqdefault.jpg"}},
        },
    }


def _video(video_id: str, content: str = "live", viewers=12, end: str | None = None) -> dict:
    live = {"actualStartTime": "2026-11-17T00:00:00Z", "concurrentViewers": viewers}
    if end:
        live["actualEndTime"] = end
    return {
        "id": video_id,
        "snippet": {"liveBroadcastContent": content},
        "liveStreamingDetails": live,
    }


class Resp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> dict:
        return self._body


class FakeClient:
    def __init__(self, responses: list[Resp]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise httpx.ConnectError("no response")
        return self.responses.pop(0)


class ParseTests(unittest.TestCase):
    def test_search_keeps_live_video_ids_only(self) -> None:
        rows = rows_from_search(
            {
                "items": [
                    _search_item("abcdefghijk"),
                    _search_item("upcoming1111", "upcoming"),
                    {"id": {"kind": "youtube#channel", "channelId": "UC1"}},
                    _search_item("bad id"),
                ]
            }
        )
        self.assertEqual([row["video_id"] for row in rows], ["abcdefghijk"])
        self.assertEqual(rows[0]["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        quoted = _search_item("bcdefghijkl", title="Say &quot;hello&quot;")
        decoded = rows_from_search({"items": [quoted]})
        self.assertEqual(decoded[0]["title"], 'Say "hello"')
        self.assertTrue(rows[0]["thumbnail_url"].startswith("https://i.ytimg.com/"))

    def test_details_drop_ended_and_missing_and_parse_viewers(self) -> None:
        rows = rows_from_search({"items": [_search_item("abcdefghijk"), _search_item("bcdefghijkl")]})
        kept = apply_video_details(
            rows,
            {"items": [_video("abcdefghijk", viewers="1200"), _video("bcdefghijkl", end="2026-11-17T01:00:00Z")]},
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["concurrent_viewers"], 1200)
        self.assertEqual(kept[0]["started_at"], "2026-11-17T00:00:00Z")

    def test_rejects_non_youtube_thumbnail(self) -> None:
        item = _search_item("abcdefghijk")
        item["snippet"]["thumbnails"]["medium"]["url"] = "https://evil.example/a.jpg"
        rows = rows_from_search({"items": [item]})
        self.assertEqual(rows[0]["thumbnail_url"], "")

    def test_own_broadcasts_keep_live_only(self) -> None:
        rows = rows_from_broadcasts(
            {
                "items": [
                    {"id": "abcdefghijk", "status": {"lifeCycleStatus": "live"}, "snippet": {"title": "Mine", "channelTitle": "Me"}},
                    {"id": "bcdefghijkl", "status": {"lifeCycleStatus": "complete"}, "snippet": {"title": "Old"}},
                    {"id": "cdefghijklm", "status": {"lifeCycleStatus": "testing"}, "snippet": {"title": "Test"}},
                ]
            }
        )
        self.assertEqual([row["video_id"] for row in rows], ["abcdefghijk"])
        self.assertTrue(rows[0]["own"])

    def test_own_row_wins_over_the_public_sample(self) -> None:
        own = [{"video_id": "abcdefghijk", "own": True}]
        public = [{"video_id": "abcdefghijk", "own": False}, {"video_id": "bcdefghijkl", "own": False}]
        merged = merge_streams(own, public)
        self.assertEqual([row["own"] for row in merged], [True, False])
        self.assertEqual(len(merged), 2)

    def test_filters_and_ttl(self) -> None:
        self.assertEqual(clean_filters("  news  ", "us", "25"), ("news", "US", "25"))
        self.assertEqual(public_ttl(""), 45 * 60)
        self.assertEqual(public_ttl("news"), 10 * 60)
        with self.assertRaises(AppError):
            clean_filters("x", "USA", "")
        self.assertEqual(quota_reason(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}}), "quotaExceeded")
        self.assertIsNone(quota_reason(500, {"error": {"errors": [{"reason": "quotaExceeded"}]}}))


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_live_cache()

    def tearDown(self) -> None:
        clear_live_cache()

    def test_one_search_page_is_cached_and_confirmed(self) -> None:
        client = FakeClient(
            [
                Resp(200, {"regionCode": "US", "pageInfo": {"totalResults": 999999}, "items": [_search_item("abcdefghijk")]}),
                Resp(200, {"items": [_video("abcdefghijk")]}),
            ]
        )
        first = load_discovery("key", "", "", "", None, client=client, now=100.0)
        self.assertFalse(first["cached"])
        self.assertEqual(first["region"], "US")
        self.assertEqual(len(first["streams"]), 1)
        self.assertNotIn("totalResults", first)
        self.assertEqual(len(client.calls), 2)
        self.assertNotIn("pageToken", client.calls[0][1])
        self.assertEqual(client.calls[0][1]["eventType"], "live")
        self.assertEqual(client.calls[0][1]["maxResults"], 25)
        second = load_discovery("key", "", "", "", None, client=client, now=101.0)
        self.assertTrue(second["cached"])
        self.assertEqual(len(client.calls), 2)

    def test_quota_does_not_include_the_key(self) -> None:
        client = FakeClient([Resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "key=secret"}})])
        with self.assertRaises(AppError) as caught:
            load_discovery("secret-key", "news", "", "", None, client=client, now=1.0)
        self.assertEqual(caught.exception.code, "YOUTUBE_SEARCH_QUOTA")
        self.assertNotIn("secret", caught.exception.message)

    def test_own_failure_keeps_the_public_sample(self) -> None:
        client = FakeClient(
            [
                Resp(200, {"regionCode": "US", "items": [_search_item("abcdefghijk")]}),
                Resp(200, {"items": [_video("abcdefghijk")]}),
                Resp(403, {"error": {"errors": [{"reason": "insufficientPermissions"}]}}),
            ]
        )
        result = load_discovery("key", "", "", "", "token", client=client, now=5.0)
        self.assertEqual([row["video_id"] for row in result["streams"]], ["abcdefghijk"])
        self.assertFalse(result["streams"][0]["own"])
