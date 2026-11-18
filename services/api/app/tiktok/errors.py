"""Classify TikTok Direct Post errors. Pure: no network."""

from __future__ import annotations

BATCH_STOP_CODES = frozenset(
    {
        "spam_risk_too_many_posts",
        "spam_risk_user_banned_from_posting",
        "reached_active_user_cap",
        "unaudited_client_can_only_post_to_private_accounts",
        "TIKTOK_DISCONNECTED",
    }
)

ROW_FAIL_CODES = frozenset(
    {
        "file_format_check_failed",
        "duration_check_failed",
        "frame_rate_check_failed",
        "picture_size_check_failed",
        "spam_risk_text",
        "spam_risk",
        "privacy_level_option_mismatch",
        "invalid_param",
    }
)

class RetryLater(Exception):
    """Same row, later. Do not call init again when publish_id is already stored."""


class BatchStop(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class RowFail(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class TikTokAPIError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


MESSAGES = {
    "spam_risk_too_many_posts": "TikTok will not take more posts from this account right now. Try again later.",
    "spam_risk_user_banned_from_posting": "TikTok has blocked posting on this account.",
    "reached_active_user_cap": "TikTok's daily limit for posting accounts is reached.",
    "unaudited_client_can_only_post_to_private_accounts": (
        "This TikTok app is not audited. Use a private account and choose Only me."
    ),
    "privacy_level_option_mismatch": "That privacy option is not available for this account.",
    "TIKTOK_DISCONNECTED": "TikTok is disconnected. Connect the account again. Posts already sent are unchanged.",
    "rate_limit_exceeded": "TikTok rate limit. This post will retry. It was not started again.",
}


def classify_tiktok(code: str) -> str:
    """stop_batch, fail_row, retry, or disconnected. retry never starts a second init."""
    if code == "rate_limit_exceeded":
        return "retry"
    if code in {"access_token_invalid", "scope_not_authorized"}:
        return "disconnected"
    if code in BATCH_STOP_CODES:
        return "stop_batch"
    return "fail_row"


def stops_batch(code: str | None) -> bool:
    return code in BATCH_STOP_CODES


def raise_classified(exc: TikTokAPIError) -> None:
    """Turn a TikTok error into a row outcome. 5xx retries the same publish_id."""
    if exc.status >= 500 and not exc.code:
        raise RetryLater() from exc
    kind = classify_tiktok(exc.code)
    text = message_for(exc.code, exc.message)
    if kind == "disconnected":
        from app.tiktok.oauth import TikTokDisconnected

        raise TikTokDisconnected() from exc
    if kind == "stop_batch":
        raise BatchStop(exc.code, text) from exc
    if kind == "retry":
        raise RetryLater() from exc
    if exc.status >= 500:
        raise RetryLater() from exc
    raise RowFail(exc.code or "TIKTOK_ERROR", text) from exc


def message_for(code: str, fallback: str = "") -> str:
    return MESSAGES.get(code) or fallback or code
