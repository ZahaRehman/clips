"""Map yt-dlp live_status to the live-route responses. No network."""

from __future__ import annotations

# Accept only this value. Everything else is a refusal.
ACCEPT = "is_live"

_REFUSALS: dict[str, tuple[str, str]] = {
    "is_upcoming": ("LIVE_NOT_STARTED", "This premiere has not started."),
    "post_live": (
        "LIVE_POSTPROCESSING",
        "YouTube is still turning this into a video. Wait, then use the normal URL form.",
    ),
    "was_live": ("NOT_LIVE", "This is not live. Use the normal URL form."),
    "not_live": ("NOT_LIVE", "This is not live. Use the normal URL form."),
}

_OTHER = ("NOT_LIVE", "This is not live. Use the normal URL form.")


def refusal_for(live_status: str | None) -> tuple[str, str] | None:
    """Return (code, message) when capture must not start. None means is_live."""
    status = (live_status or "").strip()
    if status == ACCEPT:
        return None
    return _REFUSALS.get(status, _OTHER)
