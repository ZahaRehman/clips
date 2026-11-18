# Phase 3 — Reframe & Caption Polish

**Depends on:** Phase 2 complete  
**Estimated effort:** ~1 week  
**Architecture refs:** §7.6–7.8, §12 quality gates, R&D §6 safe zones  

---

## 0. Goal

Make exported clips look **personally postable** for talking-head content:

1. **Face-aware 9:16 crop** (MediaPipe) with manual overrides.  
2. **Karaoke-style ASS** (word highlight) in safe zones.  
3. **Caption text overrides** before export.  
4. **Loudnorm** + idempotent re-export after trim/caption edits.  
5. Optional **WhisperX** alignment upgrade behind a flag.

---

## 1. Out of scope

- Full active-speaker detection / split layouts  
- Remotion motion templates (Phase 4 spike only)  
- Social scheduling / analytics  
- Multi-user  

---

## 2. Tasks

### Task 3.1 — MediaPipe face crop

**Do:** `pipeline/reframe.py`
- Sample N frames inside clip window from proxy (e.g. every 0.5–1.0s).  
- Detect faces; compute median center (x,y).  
- Build 9:16 crop window on source aspect; clamp to frame.  
- Fallback: center crop if no faces.  
- Store on clip: `crop` JSON `{ "mode": "face"|"center"|"left"|"right"|"custom", "x", "y", "w", "h" }` (normalized or pixel—document which).  
- Multi-face: pick largest face or median of centers; document choice.

**Done when:** On 3 talking-head samples, face stays in frame better than pure center (manual visual check).

---

### Task 3.2 — Crop overrides API + UI

**Do:**
- `PATCH /clips/{id}` accepts `crop.mode`: `face` | `center` | `left` | `right` | `custom` (+ custom box if custom).  
- UI toggle for crop mode; optional preview still (proxy crop preview is nice-to-have).  
- Recompute face crop endpoint or recompute on export if mode=`face`.

**Done when:** Switching to `left`/`right`/`center` changes export framing.

---

### Task 3.3 — Karaoke ASS

**Do:** Upgrade captions generator:
- 1–4 words per page.  
- Highlight current word (`\k` or color change).  
- White fill, black outline, heavy sans; **bundle font**.  
- Position lower-center **inside safe zone** (avoid bottom ~420px / top ~240px / right ~200px on 1080×1920—tune and document).  
- Regenerate ASS whenever trim or override changes.

**Done when:** Exported clip shows word-level highlight readable on mute.

---

### Task 3.4 — Caption override

**Do:**
- Field `caption_text_override` on clip (plain text or simple timed JSON—prefer plain text replacing full clip captions **or** per-word edits; document).  
- Minimum viable: textarea to replace caption string for the clip (even if timing stays from words)—better: edit words list JSON in UI.  
- Prefer: editable transcript span for the clip that regenerates ASS.  
- API: PATCH clip with override; export uses override path.

**Done when:** Fixing a Whisper typo and re-exporting shows corrected text on video.

---

### Task 3.5 — Loudnorm + export idempotency

**Do:**
- Add FFmpeg loudnorm (or `loudnorm` single-pass) in export graph.  
- Input hash: trim + crop + captions content + preset → if export exists and hash matches, skip encode.  
- `POST /clips/{id}/export` force=`true` to rebuild.  
- After trim PATCH, mark previous export stale (`status=pending` or delete ready flag).

**Done when:** Re-export after trim changes duration; identical re-export is fast/no-op.

---

### Task 3.6 — Optional WhisperX

**Do:**
- Env `ASR_ALIGNER=none|whisperx`.  
- If whisperx: tighter word times after faster-whisper (or replace path).  
- Keep default `none` so Phase 1/2 CPU boxes don’t break.  
- Document extra deps / RAM.

**Done when:** Default path unchanged; whisperx path documented and optional.

---

### Task 3.7 — Delete / reprocess UX

**Do:**
- Delete project (and files) endpoint if missing.  
- Reprocess from `captions`/`export` without full ASR.  
- UI: delete clip, re-export button.

**Done when:** Operator can fix one clip without re-transcribing the hour.

---

### Task 3.8 — Docs & visual QA checklist

**Do:** README section “Caption & crop quality”.  
Checklist for operator:
- [ ] Face centered on 3 videos  
- [ ] Captions in safe zone on iPhone preview mental model  
- [ ] Typo fix → re-export works  

Update progress tracker.

---

## 3. Parallelization

| Agent A | Agent B |
| --- | --- |
| 3.1–3.2 reframe | 3.3–3.4 captions |
| Then one agent: 3.5–3.8 | |

---

## 4. Exit criteria

- [ ] Face mode works on ≥3 talking-head tests; center fallback on screen-share.  
- [ ] Crop mode overrides work.  
- [ ] Karaoke ASS visible; font not missing in Docker.  
- [ ] Caption override → re-export shows fix.  
- [ ] Idempotent export / force rebuild works.  
- [ ] Default install still works without WhisperX.  
- [ ] Progress tracker: Phase 3 = `done`.

---

## 5. Agent completion report

```text
Phase 3 complete.
- Face crop notes: normalized 0–1 source box; largest face per sample, then median center; center fallback when empty. Geometry checked for left, center, and right face positions. Live MediaPipe on operator footage is still a visual pass.
- Font used for ASS: DejaVu Sans (bundled Bold + regular)
- WhisperX enabled in default compose: no (expected)
- Next: docs/plans/PHASE_4_HARDENING.md
```
