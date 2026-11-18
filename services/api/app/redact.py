import re

_URL = re.compile(r"https?://\S+")


def scrub_text(text: str) -> str:
    """Drop URLs so logs and error strings do not keep query tokens."""
    return _URL.sub("[url]", text or "")
