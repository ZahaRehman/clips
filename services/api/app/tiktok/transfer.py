"""PUT one Content-Range span to a TikTok upload URL. The URL is never logged."""

from __future__ import annotations

import time

import httpx

from app.tiktok.errors import RetryLater, RowFail

_CHUNK_BACKOFF = (2, 4, 8)
EXPIRED = "The TikTok upload link expired. Check the TikTok app before trying again. Cliping did not start a second post."


def put_span(upload_url: str, path: str, start: int, end: int, total: int) -> None:
    payload = _read_span(path, start, end)
    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(len(payload)),
        "Content-Range": f"bytes {start}-{end}/{total}",
    }
    for attempt, delay in enumerate((0, *_CHUNK_BACKOFF)):
        if delay:
            time.sleep(delay)
        try:
            response = httpx.put(upload_url, content=payload, headers=headers, timeout=120, follow_redirects=False)
        except httpx.HTTPError as exc:
            if attempt == len(_CHUNK_BACKOFF):
                raise RetryLater() from exc
            continue
        if response.status_code in {200, 201, 206}:
            return
        if response.status_code == 416:
            reported = _range_end(response.headers.get("content-range"))
            if reported is None or reported > end:
                raise RetryLater()
            if reported >= end + 1:
                return
            payload = _read_span(path, reported, end)
            headers["Content-Length"] = str(len(payload))
            headers["Content-Range"] = f"bytes {reported}-{end}/{total}"
            continue
        if response.status_code == 403:
            raise RowFail("UPLOAD_EXPIRED", EXPIRED)
        if response.status_code >= 500:
            if attempt == len(_CHUNK_BACKOFF):
                raise RetryLater()
            continue
        raise RowFail("UPLOAD_REJECTED", "TikTok rejected the upload. Check the TikTok app before trying again.")


def _read_span(path: str, start: int, end: int) -> bytes:
    with open(path, "rb") as handle:
        handle.seek(start)
        return handle.read(end - start + 1)


def _range_end(header: str | None) -> int | None:
    if not header or "bytes" not in header or "-" not in header:
        return None
    try:
        span = header.split("bytes", 1)[1].strip().split("/")[0]
        end = span.split("-", 1)[1]
        return int(end) + 1
    except (IndexError, ValueError):
        return None
