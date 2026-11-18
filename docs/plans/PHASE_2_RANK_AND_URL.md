# Phase 2 — Auto Proposals + YouTube URL Convenience

**Depends on:** Phase 1 complete and demoable  
**Estimated effort:** ~1–2 weeks  
**Architecture refs:** §1 ingest policy, §7.4–7.5, §8 API, §11 LLM env, §12 review UI  

---

## 0. Goal

1. **Auto-propose** clip windows from transcript (heuristics + optional local Ollama).  
2. **Review gallery:** accept / reject / tweak.  
3. **Optional URL ingest** via yt-dlp behind an explicit rights/ToS disclaimer.  
4. Better **job progress** (poll improvements or SSE).

End state: paste URL *or* upload → ≥5 proposals → accept ≥2 → export both → download.

---

## 1. Out of scope

- MediaPipe face tracking polish (Phase 3)  
- Caption text override editor (Phase 3)  
- Autopost to socials  
- Multi-tenant auth  
- DRM bypass / cookie harvesting / age-gate circumvention  

If yt-dlp fails (blocked, DRM, geo): **fail cleanly** and tell user to upload a file they have rights to.

---

## 2. Legal / product constraints (must implement)

- UI copy: operator is responsible for rights; prefer owned uploads.  
- URL path requires checkbox: disclaimer accepted **before** enqueue.  
- Persist `source_meta.disclaimer_accepted_at` (ISO timestamp).  
- Env `ALLOW_URL_INGEST` must be `true` to enable URL route; if `false`, API returns 403 with clear message.  
- Caps: `MAX_SOURCE_DURATION_SEC`, `MAX_SOURCE_BYTES` still apply.  
- Do not document or implement ToS/DRM bypasses.

---

## 3. Pipeline changes

After ASR, pipeline continues:

```text
asr → segment_score → propose_windows → ready_for_review
```

| Stage | Behavior |
| --- | --- |
| `segment_score` | Build `candidates.json` via heuristics; if `LLM_PROVIDER!=none`, refine/rank with LLM JSON schema |
| `propose_windows` | Snap to speech boundaries; insert `clips` rows `status=proposed` |
| Manual clips from Phase 1 | Still allowed (`POST .../clips` → `accepted`) |

**Never** overwrite clips with status `accepted` / `exported` / `rejected` when re-running proposals—only replace `proposed`.

---

## 4. Tasks

### Task 2.1 — Heuristic scorer

**Do:** `pipeline/score.py`  
Inputs: segments/words.  
Signals (implement at least 4):
- Question marks / interrogative starts  
- Exclamation / high-energy punctuation  
- Pause gaps > ~0.4s as boundaries  
- Keyword boost list (configurable): e.g. `tip`, `mistake`, `secret`, `why`, `how`, `lesson`  
- Prefer sentence starts; penalize mid-clause ends  
- Target duration clamp using `CLIP_MIN_SEC`/`CLIP_MAX_SEC` (default 20–60)  
- Produce top `TARGET_CLIP_COUNT` (default 8) candidates with `score`, `title` (heuristic), `reason`

Output: `transcript/candidates.json`.

**Done when:** Unit tests on fixture transcript yield ≥3 candidates with valid windows.

---

### Task 2.2 — Ollama LLM ranker (optional path)

**Do:**
- `LLM_PROVIDER`: `none` | `ollama` | `openai_compatible`  
- If `none`: heuristics only.  
- If `ollama`: POST to `{OLLAMA_BASE_URL}/api/chat` (or generate) with strict JSON schema prompt; model `OLLAMA_MODEL`.  
- Validate: duration bounds, non-overlap (max 5s), within media length, parse JSON safely (repair once or fail to heuristics).  
- Cache key: transcript checksum + prompt version + provider/model.  
- Timeouts + clear errors; on LLM failure **fall back to heuristics** (don’t fail whole pipeline).

**Done when:** With Ollama running locally, candidates include LLM titles/reasons; with Ollama down, heuristics still finish.

---

### Task 2.3 — Speech-boundary snap

**Do:** `pipeline/windows.py`  
- Move start to previous word boundary / pause ≥250ms.  
- Move end to next boundary; pad ±150–300ms without exceeding neighbors.  
- Never start mid-word.  
- Write `clips` rows: rank, t_start, t_end, score, title, reason, status=`proposed`.

**Done when:** Proposed clips don’t start mid-word on fixture with timestamps.

---

### Task 2.4 — Clip review API

**Do:**
- `PATCH /api/v1/clips/{id}` supports `status`: `accepted` | `rejected`, trim edits, title.  
- `POST /api/v1/projects/{id}/propose` (optional) to re-run score+propose from existing transcript.  
- List clips sorted by `rank`/`score`.  
- Export remains only for `accepted` (or allow export from proposed after auto-accept—prefer explicit accept).

**Done when:** Accept 2 → export 2 works via API.

---

### Task 2.5 — Review gallery UI

**Do:** Minimum from architecture §12.2:
1. Gallery of proposals: title, score, reason, duration.  
2. Click → player seeks to window; show in/out.  
3. Transcript highlight for span; adjust handles/inputs.  
4. Accept / Reject / Edit title.  
5. Export accepted; download when ready.  
6. Pipeline progress + errors visible.

Keep Phase 1 manual “create clip” as secondary action.

**Done when:** Browser-only flow meets Goal §0 without curl.

---

### Task 2.6 — URL ingest (yt-dlp)

**Do:**
- `POST /api/v1/projects` JSON body alternative: `{ "url": "...", "disclaimer_accepted": true, "title"? }`  
  - Or multipart with `url` + `disclaimer_accepted` fields—pick one, document it.  
- Reject if `ALLOW_URL_INGEST` is false.  
- Reject if `disclaimer_accepted` is not true.  
- Worker `ingest` uses yt-dlp: best video+audio merge into `raw/`.  
- Store metadata: title, duration, uploader, webpage_url, disclaimer_accepted_at.  
- Size/duration caps; on failure set project `failed` with actionable message (including “upload a file instead”).  
- Add `yt-dlp` to worker image/deps.  
- UI: URL field + required disclaimer checkbox + link to short rights note.

**Done when:** With flag on + disclaimer, a public non-DRM test URL the operator is allowed to use (or their own upload mirror) ingests; blocked cases fail cleanly.

**Testing note:** Prefer testing URL ingest on content the operator owns (their YouTube upload) when possible.

---

### Task 2.7 — Progress UX

**Do:**
- Ensure `GET /projects/{id}` returns `stage`, `progress` 0–1, `error`.  
- Optional: `GET /api/v1/projects/{id}/events` SSE for stage updates.  
- UI: progress bar / stage label (`normalize`, `asr`, `segment_score`, …).

**Done when:** User can tell ASR vs ranking vs done without guessing.

---

### Task 2.8 — Checkpoints / resume

**Do:**
- `POST /api/v1/projects/{id}/reprocess` body `{ "from_stage": "asr"|"segment_score"|... }`.  
- Idempotent skips for unchanged artifacts.  
- Document in README.

**Done when:** Re-run from `segment_score` does not re-download or re-ASR if artifacts present.

---

### Task 2.9 — Docs & smoke

**Do:**
- README: Ollama setup (`ollama pull ...`), `LLM_PROVIDER`, `ALLOW_URL_INGEST`, disclaimer behavior.  
- Update plans progress tracker.  
- Smoke script: upload path still works with `LLM_PROVIDER=none`.

**Done when:** Phase 1 happy path unbroken; Phase 2 features documented.

---

## 5. Env vars (Phase 2 additions)

| Variable | Example |
| --- | --- |
| `ALLOW_URL_INGEST` | `true` |
| `YT_DLP_PATH` | `yt-dlp` |
| `LLM_PROVIDER` | `ollama` or `none` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | `llama3.1:8b` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | only if `openai_compatible` |
| `TARGET_CLIP_COUNT` | `8` |
| `CLIP_MIN_SEC` / `CLIP_MAX_SEC` | `20` / `60` |

---

## 6. Parallelization

| Agent A | Agent B |
| --- | --- |
| 2.1–2.3, 2.8 scoring/snap/checkpoints | 2.4–2.5 review API/UI |
| 2.6 URL ingest (after API create-project contract agreed) | 2.7 progress UX |

Finalize create-project request shapes before splitting A/B on 2.6 vs UI.

---

## 7. Exit criteria

- [ ] Upload → ≥5 `proposed` clips with scores/titles/reasons.  
- [ ] Accept 2 → export both → download.  
- [ ] `LLM_PROVIDER=none` still proposes via heuristics.  
- [ ] Ollama path works when available; falls back if not.  
- [ ] URL ingest gated by env + disclaimer; acceptance timestamp stored.  
- [ ] Failures on blocked URLs are clear; no bypass code.  
- [ ] Phase 1 manual clip path still works.  
- [ ] Progress tracker: Phase 2 = `done`.

---

## 8. Agent completion report

```text
Phase 2 complete.
- Heuristic-only verified: yes/no
- Ollama verified: yes/no
- URL ingest verified: yes/no (note source used)
- Next: docs/plans/PHASE_3_REFRAME_POLISH.md
```
