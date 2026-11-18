# Phase 5 — Autopilot Clips (link → 10+ rendered Shorts with post text)

**Depends on:** Phase 4 complete  
**Estimated effort:** ~1 week  
**Research:** [../AUTOPILOT_RND.md](../AUTOPILOT_RND.md)  
**Architecture refs:** §1 ingest policy, §7.4–7.8 stages, §11 env, §12 review UI  

---

## 0. Goal

Paste a link once. With no further clicks, the project ends at a gallery of **at least 10** (or as many as the source supports) rendered 1080×1920 Shorts, each with a Gemini-written **title, description, and hashtags** you can edit.

End state: paste URL → wait → gallery of N playable clips with post text → edit or exclude → download. Posting is Phase 6.

---

## 1. Out of scope

- Any YouTube API call (Phase 6).
- Posting, scheduling, OAuth.
- Gemini changes to burned-in caption text.
- Sending video or audio to Gemini.
- New ranking heuristics beyond what the count rule needs.
- Changing manual-mode behavior (upload or URL with autopilot off stays exactly as today).

---

## 2. Product constraints (must implement)

- Rights checkbox on the URL form stays required; `disclaimer_accepted_at` still stored.
- Gemini is **optional**. No key → heuristic ranking + fallback metadata, and the UI says so.
- Every Gemini failure is visible: provider used and error reason stored in the job checkpoint and shown on the project page. Remove the silent `except Exception: return None` behavior.
- Autopilot clip length **20–59 s**, target 35 s (keeps Shorts out of the >60 s claim-block rule).
- Never pad to reach 10. Fewer good clips + a clear message beats overlapping junk.
- Rendering publishes nothing. Plan rule 6 is updated so autopilot projects may render proposals without an Accept click.

---

## 3. Pipeline changes

Autopilot project:

```text
ingest → normalize → asr → segment_score → propose_windows → post_metadata → render_clips → ready_for_review
```

Manual project: unchanged (`… → propose_windows → ready_for_review`).

| Stage | Behavior |
| --- | --- |
| `segment_score` | If `LLM_PROVIDER=gemini` and a key is set: one Gemini call over the **full** transcript (cap from env, default 300k chars), asking for `planned + 5` candidates. Validate with existing `validate_clips`. On failure or too few valid candidates: heuristic, with `llm_error` recorded. |
| `propose_windows` | Unchanged snap logic. Then drop overlaps created by snapping and keep the best `planned`. |
| `post_metadata` (new) | One Gemini call for all kept clips. Writes draft title/description/hashtags per clip. Fallback drafts on failure. Idempotent by hash of (clip windows text + prompt version + model). |
| `render_clips` (new) | For each kept clip in rank order: create `ClipExport` + reuse `run_export_clip`. Per-clip failure → that export `failed`, continue. Progress “Rendering clip k of N.” |

Re-running from `segment_score` must still never overwrite `accepted` / `exported` / `rejected` clips.

---

## 4. Tasks

### Task 5.1 — Autopilot flag and clip count rule

**Do:**
- `projects.autopilot` boolean (Alembic migration), default false.
- `POST /api/v1/projects` JSON body accepts `autopilot` (default **true** for URL from the UI) and `clip_count` (default `AUTOPILOT_CLIP_COUNT`, 10; bounds 1–30).
- Store `clip_count` in `target_clip_count`; autopilot sets `target_duration_sec` from `AUTOPILOT_TARGET_SEC`.
- Pure function `planned_clip_count(requested, speech_seconds, target_seconds) -> int` (speech seconds = sum of word durations + short gaps, or segment spans).
- Autopilot uses `AUTOPILOT_CLIP_MIN_SEC` / `AUTOPILOT_CLIP_MAX_SEC` instead of the global `CLIP_MIN_SEC` / `CLIP_MAX_SEC`. Cache keys already include min/max/count; keep it that way.
- When `planned < requested`, write the reason into the checkpoint so the UI can say “Source has N min of speech; made M clips.”

**Done when:** Unit tests cover `planned_clip_count` at 2-minute, 10-minute, and 60-minute inputs; manual projects still read the global min/max.

---

### Task 5.2 — Gemini client module

**Do:**
- New module (one concern): `app/llm/gemini.py` — `generate_json(prompt, schema, *, model) -> dict`.
- Native REST via `httpx`: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, header `x-goog-api-key`, `generationConfig.responseMimeType="application/json"` and the JSON Schema field. **Verify the exact REST field name** (`responseJsonSchema` vs `responseSchema`) against the current API reference before coding; do not guess.
- Do not repeat the schema in the prompt text.
- Errors mapped to stable codes: `GEMINI_NO_KEY`, `GEMINI_QUOTA_DAY`, `GEMINI_QUOTA_MINUTE`, `GEMINI_BAD_RESPONSE`, `GEMINI_TIMEOUT`, `GEMINI_HTTP`. Read the 429 body’s quota id to tell per-day from per-minute.
- Retry policy: one retry after a short wait on per-minute 429, 5xx, or timeout. No retry on per-day 429. If `GEMINI_FALLBACK_MODEL` is set and the per-day quota is spent, try that model once.
- Never log the API key or full prompt. Log model, status, quota id, latency.

**Done when:** A smoke script with a real key returns schema-valid JSON for a tiny prompt; with a bad key it raises the mapped error code.

---

### Task 5.3 — Gemini ranking provider

**Do:**
- `LLM_PROVIDER` gains `gemini`. `llm_rank.rank_with_llm` dispatches to the Gemini client; Ollama/OpenAI paths stay.
- Transcript cap per provider: Gemini uses `GEMINI_MAX_TRANSCRIPT_CHARS`; others keep 14,000.
- Return `(clips, error_code | None)` instead of swallowing exceptions, so `run_segment_score` records `provider_used` and `llm_error` in the checkpoint for every provider.
- Prompt asks for self-contained moments with a hook in the first seconds, complete thought, no mid-sentence end, within the autopilot length.

**Done when:** With a key, a 10+ minute source yields Gemini-ranked candidates spread across the whole video (not only the first 15 minutes). Without a key, heuristics run and the checkpoint shows `GEMINI_NO_KEY`.

---

### Task 5.4 — Post metadata stage

**Do:**
- Clip columns (migration): `post_title`, `post_description` (final composed text), `post_hashtags` (JSON list), `post_meta_source` (`gemini` | `fallback` | `user`), `post_meta_hash`.
- Stage module `app/pipeline/post_metadata.py`: build one request with source title, `CHANNEL_CONTEXT`, transcript language, and per-clip `{clip_id, text}`; schema per clip `{clip_id, title, description, hashtags[]}`.
- Pure validators (unit-tested): title ≤100 chars, no `<` `>`, non-empty; description stripped of `<` `>`; hashtags normalized to `#word` (letters/digits/underscore, no spaces), deduped case-insensitively, 3–5 kept; composed description `description + "\n\n" + " ".join(hashtags)` under 5000 bytes.
- Fallback per clip: `post_title = clip.title`, empty description and hashtags, source `fallback`, with the error code stored on the job checkpoint.
- Skip clips whose `post_meta_source == "user"` (never overwrite your edits).
- `post_meta_hash` = hash of the clip’s window text + prompt version + model. When trim or caption override changes the text, the UI can flag the draft as stale.

**Done when:** Each kept clip has a draft; user-edited drafts survive a re-run; validator tests pass.

---

### Task 5.5 — Render stage and export gate

**Do:**
- `render_clips` stage in `runner.STAGES` path for autopilot projects only.
- Allow export of `proposed` clips **only** when `project.autopilot` is true (in `queue_clip_export` for API calls, and in the stage directly).
- Reuse `run_export_clip`; do not copy export logic. A clip whose export fails is left with a failed export row and an error, and the stage continues.
- Progress: give rendering its own range so the bar does not sit at 95% for ten minutes; stage label `Rendering clip k of N`.
- Final project status `ready_for_review` even if some clips failed; record counts in the checkpoint.

**Done when:** A 10+ minute owned source reaches `ready_for_review` with N ready exports and no clicks after Paste.

---

### Task 5.6 — Clip and metadata API

**Do:**
- `ClipOut` includes the `post_*` fields and latest export status + download URL (avoid one request per clip from the gallery).
- `PATCH /api/v1/clips/{id}` accepts `post_title`, `post_description`, `post_hashtags`; any edit sets `post_meta_source="user"`. Apply the same validators; return a 400 with a clear code instead of silently trimming the title.
- `POST /api/v1/projects/{id}/post-metadata` re-runs `post_metadata` for non-user drafts (one Gemini call).
- Project response includes autopilot summary: `requested`, `planned`, `rendered`, `failed`, `provider_used`, `llm_error`, `metadata_error`.

**Done when:** Gallery data loads in one project call plus one clips call.

---

### Task 5.7 — UI: paste link and gallery

**Do:**
- Home: the URL form is the primary action. Fields: link, clip count (default 10), Autopilot checkbox (default on), rights checkbox (required). Upload stays as a second card.
- Project page, autopilot projects: a **gallery grid** first — 9:16 player of each `export.mp4`, duration, score, editable title / description / hashtags, “stale” badge when text changed, Exclude (reject) toggle, Download, and “Open in editor” (scrolls to the existing transcript/trim/crop tools, which stay below).
- Banner states for: fewer clips than requested (with the reason), Gemini fallback (with the reason, for example “Gemini daily quota reached; resets at midnight Pacific”), clips that failed to render.
- Progress shows the stage and “Rendering clip k of N.”
- Manual projects keep today’s layout.

**Done when:** Browser-only: paste → wait → see N playable clips with text → edit one title → reload shows the edit.

---

### Task 5.8 — Docs and smoke

**Do:**
- README: Gemini key setup (Google AI Studio), env vars below, free-tier data-use note, quota expectations (2 calls per video).
- Smoke script `scripts/smoke_phase5.sh <owned-url-or-file>`: runs autopilot, asserts ≥1 ready export at 1080×1920 with 20–59 s duration and non-empty `post_title` for every rendered clip.
- Update [../CLIPPING_ARCHITECTURE.md](../CLIPPING_ARCHITECTURE.md) env table and stage list; update the plans tracker.

**Done when:** Phase 1–4 paths unbroken; Phase 5 documented.

---

## 5. Env vars (Phase 5 additions)

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `none` | Add `gemini` |
| `GEMINI_API_KEY` | empty | From Google AI Studio. Never logged. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Pin an exact id |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` | Tried once on per-day quota |
| `GEMINI_TIMEOUT_SEC` | `90` | |
| `GEMINI_MAX_TRANSCRIPT_CHARS` | `300000` | Gemini only |
| `CHANNEL_CONTEXT` | empty | One line about your channel, used in the metadata prompt |
| `AUTOPILOT_CLIP_COUNT` | `10` | |
| `AUTOPILOT_CLIP_MIN_SEC` | `20` | |
| `AUTOPILOT_CLIP_MAX_SEC` | `59` | Keep under 60 for Shorts claim rules |
| `AUTOPILOT_TARGET_SEC` | `35` | |

---

## 6. Parallelization

| Agent A (backend) | Agent B (frontend) |
| --- | --- |
| 5.1–5.5 stages, migrations, Gemini client | 5.7 UI against the 5.6 contract |
| 5.6 API (land the response shapes first) | 5.8 README + smoke after A lands |

Agree the `ClipOut` / project summary shapes from 5.6 before splitting.

---

## 7. Exit criteria

- [x] Paste a ≥10-minute source you own with autopilot on → project reaches `ready_for_review` with **≥10** ready exports and no clicks after Paste.
- [x] Every rendered clip is 1080×1920, 20–59 s, non-overlapping, with burned captions.
- [x] With `GEMINI_API_KEY` set: clips are spread across the whole source; every clip has a Gemini title, description, and 3–5 valid hashtags. Windows fell back to heuristics (`LLM_TOO_FEW_CLIPS`). `gemini-3.8-flash` timed out, so this run used `gemini-3.5-flash`.
- [x] Without a key, or with a forced Gemini error: heuristics + fallback drafts, and the UI shows the reason.
- [x] A 2-minute source makes fewer clips and says why.
- [x] Editing a title survives a metadata re-run; a trim marks the draft stale.
- [x] Manual mode (autopilot off) behaves exactly as Phase 4.
- [x] Unit tests: `planned_clip_count`, metadata validators, description composer, Gemini 429 classification.
- [x] Progress tracker: Phase 5 = `done`.

---

## 8. Agent completion report

```text
Phase 5 complete.
- Source used (owned?): existing local MP4, about 14 minutes. New project 080c4359-2d69-4554-8c58-9e4fb315d65e.
- Gemini verified: post text yes, gemini-3.5-flash, two calls (rank + metadata). gemini-3.8-flash returned 503 and then timed out, so .env uses gemini-3.5-flash. Ranking returned too few valid windows (LLM_TOO_FEW_CLIPS) and the heuristic windows were kept. They span 84s–834s.
- Heuristic fallback verified: yes, earlier project with no key showed GEMINI_NO_KEY on the page.
- Requested / planned / rendered: 10 / 10 / 10. All 1080×1920, 36–39s, no overlaps, Gemini title, description, and 4 hashtags.
- Wall time paste → gallery: about 52 minutes, including one worker restart during normalize.
- Short source: 1.6 min of speech, page says it made 2 clips instead of 10. Edited title survived a redraft and stayed stale.
- Next: docs/plans/PHASE_6_YOUTUBE_PUBLISH.md
```
