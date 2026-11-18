"""Clip captions.

ASS is karaoke: 1–4 words per page, white until the word is sung, then yellow,
with a heavy black outline. On a 1080×1920 frame the text block stays inside
the short-form safe zone: 80px from the left, 200px from the right, 420px from
the bottom, and clear of the top 240px. Other sizes scale those margins.
Latin captions use bundled DejaVu Sans. Hindi uses Noto Sans Devanagari,
which the image installs. Other letters are left out so one missing glyph
cannot fail the encode.

`caption_text_override` replaces the transcript span for the clip:

- empty / null: words whose midpoint falls inside the trim
- plain text: tokens keep the original word timings when the counts match;
  otherwise they are spread evenly across that spoken span
- JSON `{"words":[{"word","start","end"}]}`: per-word text and source-timeline
  times in seconds (the same clock as the transcript)
"""

import json
from pathlib import Path
from typing import Any

CAPTION_FONT = "DejaVu Sans"
DEVANAGARI_FONT = "Noto Sans Devanagari"
SAFE_MARGIN_L = 80
SAFE_MARGIN_R = 200
SAFE_MARGIN_V = 420
SAFE_TOP = 240
MAX_WORDS_PER_PAGE = 4


def words_in_range(words: list[dict], t_start: float, t_end: float) -> list[dict]:
    selected = []
    for word in words:
        mid = (float(word["start"]) + float(word["end"])) / 2.0
        if t_start <= mid <= t_end and (word.get("word") or "").strip():
            selected.append(word)
    return selected


def resolve_caption_words(
    words: list[dict],
    t_start: float,
    t_end: float,
    override: str | None,
) -> list[dict]:
    selected = words_in_range(words, t_start, t_end)
    text = (override or "").strip()
    if not text:
        return selected
    parsed = _parse_word_override(text)
    if parsed is not None:
        return parsed
    return _apply_plain(selected, text, t_start, t_end)


def caption_pages(words: list[dict], max_words: int = MAX_WORDS_PER_PAGE) -> list[list[dict]]:
    if max_words < 1:
        max_words = 1
    return [words[i : i + max_words] for i in range(0, len(words), max_words)]


def safe_margins(width: int, height: int) -> tuple[int, int, int]:
    if width > height:
        # Whole-screen landscape export: no phone buttons on the right, captions near the bottom.
        side = int(round(120 * width / 1920))
        return side, side, int(round(80 * height / 1080))
    margin_l = max(0, int(round(SAFE_MARGIN_L * width / 1080)))
    margin_r = max(0, int(round(SAFE_MARGIN_R * width / 1080)))
    margin_v = max(0, int(round(SAFE_MARGIN_V * height / 1920)))
    return margin_l, margin_r, margin_v


def write_srt(path: Path, words: list[dict], t_start: float) -> None:
    parts: list[str] = []
    for i, cue in enumerate(caption_pages(words), start=1):
        start = float(cue[0]["start"]) - t_start
        end = float(cue[-1]["end"]) - t_start
        text = " ".join(caption_draw_text((w.get("word") or "").strip()) for w in cue).strip()
        parts.append(f"{i}\n{_srt_ts(max(0, start))} --> {_srt_ts(max(0.01, end))}\n{text}\n")
    path.write_text("\n".join(parts).strip() + ("\n" if parts else ""), encoding="utf-8")


def write_ass(path: Path, words: list[dict], t_start: float, width: int = 1080, height: int = 1920) -> None:
    margin_l, margin_r, margin_v = safe_margins(width, height)
    # Vertical: 72 px on 1920 tall. Landscape (whole screen): 56 px on 1080 tall.
    font_size = max(28, int(round(72 * height / 1920 if height >= width else 56 * height / 1080)))
    font = caption_font_name(" ".join((word.get("word") or "") for word in words))
    # Primary is the sung color (yellow). Secondary is the waiting fill (white).
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},&H0000FFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,0,2,{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header.rstrip("\n")]
    for cue in caption_pages(words):
        start = max(0.0, float(cue[0]["start"]) - t_start)
        end = max(start + 0.05, float(cue[-1]["end"]) - t_start)
        lines.append(
            f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Default,,0,0,0,,{_karaoke(cue)}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _karaoke(cue: list[dict]) -> str:
    parts: list[str] = []
    for word in cue:
        dur_cs = max(1, int(round((float(word["end"]) - float(word["start"])) * 100)))
        token = _ass_escape(caption_draw_text((word.get("word") or "").strip()))
        if not token:
            continue
        parts.append(f"{{\\k{dur_cs}}}{token}")
    return " ".join(parts)


def _parse_word_override(text: str) -> list[dict] | None:
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    rows = data.get("words") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    parsed: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        word = str(row.get("word") or "").strip()
        if not word:
            continue
        try:
            start = float(row["start"])
            end = float(row["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if end <= start:
            end = start + 0.05
        parsed.append({"word": word, "start": start, "end": end})
    return parsed or None


def _apply_plain(selected: list[dict], text: str, t_start: float, t_end: float) -> list[dict]:
    tokens = text.split()
    if not tokens:
        return selected
    if selected and len(tokens) == len(selected):
        return [{**word, "word": token} for word, token in zip(selected, tokens)]
    if selected:
        span_start = float(selected[0]["start"])
        span_end = float(selected[-1]["end"])
    else:
        span_start = t_start
        span_end = t_end
    if span_end <= span_start:
        span_end = span_start + 0.2 * len(tokens)
    step = (span_end - span_start) / len(tokens)
    return [
        {"word": token, "start": span_start + i * step, "end": span_start + (i + 1) * step}
        for i, token in enumerate(tokens)
    ]


def caption_draw_text(text: str) -> str:
    """Keep Latin and Devanagari. Drop letters the caption fonts cannot draw."""
    return "".join(ch for ch in text if _caption_char(ch))


def caption_font_name(text: str) -> str:
    if any("\u0900" <= ch <= "\u097f" for ch in text):
        return DEVANAGARI_FONT
    return CAPTION_FONT


def _caption_char(ch: str) -> bool:
    if ch.isascii():
        return True
    return "\u0900" <= ch <= "\u097f"


def _ass_escape(text: str) -> str:
    return text.replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _ass_ts(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 3600_00)
    m, rem = divmod(rem, 60_00)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def caption_payload(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "word": (word.get("word") or "").strip(),
            "start": round(float(word["start"]), 3),
            "end": round(float(word["end"]), 3),
        }
        for word in words
    ]
