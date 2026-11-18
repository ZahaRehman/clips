#!/usr/bin/env python3
"""Smoke for caption generation (no media)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.pipeline.captions import write_ass, write_srt  # noqa: E402

words = [
    {"word": "Hello", "start": 10.0, "end": 10.3},
    {"word": "world", "start": 10.3, "end": 10.7},
    {"word": "this", "start": 10.8, "end": 11.0},
    {"word": "is", "start": 11.0, "end": 11.15},
    {"word": "Cliping", "start": 11.15, "end": 11.6},
]
out = Path("/tmp/cliping_caption_smoke")
out.mkdir(exist_ok=True)
write_srt(out / "c.srt", words, 10.0)
write_ass(out / "c.ass", words, 10.0)
ass = (out / "c.ass").read_text()
assert (out / "c.srt").read_text().strip()
assert "Dialogue:" in ass
assert r"{\k" in ass
assert "DejaVu Sans" in ass
assert ",80,200,420,1" in ass
print("captions ok", out)
