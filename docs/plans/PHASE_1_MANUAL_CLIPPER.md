# Phase 1 — Manual Clipper (First Useful Product)

**Status target:** first tool you can personally use  
**Estimated effort:** ~1–2 weeks  
**Depends on:** Phase 0 complete  
**Blocks:** Phase 2–4 usefulness  
**Architecture refs:** §6 data model, §7 stages 0–2 + 6–8 (manual path), §8 API, §12 quality  

---

## 0. Goal

**Upload an owned video → transcribe with faster-whisper → pick start/end on transcript → export 9:16 MP4 with burned-in captions → download.**

No LLM. No YouTube URL ingest. Center crop is enough (face tracking is Phase 3).

**MVP success:** A 10–60 min talking-head/podcast file you own becomes at least one vertical subtitled clip.

---

## 1. Out of scope (do NOT build)

- `yt-dlp` / URL paste  
- Ollama / OpenAI ranking  
- Heuristic auto-proposals / review gallery of many candidates  
- MediaPipe face tracking (use **center crop**)  
- Karaoke word-highlight ASS polish (simple readable ASS/SRT is enough)  
- Autopost, multi-user auth, Remotion  

---

## 2. User flow (must work end-to-end)

```text
1. Open web UI
2. Upload MP4/MOV/WebM/MKV
3. See project status: ingesting → processing (normalize+ASR) → ready
4. Open project: transcript with word timings
5. Select in/out (inputs or click words) → create clip
6. Click Export
7. When ready, Download MP4 (1080×1920, captions burned in)
```

---

## 3. Data model (implement fully for Phase 1)

Implement tables per architecture §6 (fields may be nullable if unused until Phase 2):

| Table | Phase 1 required fields |
| --- | --- |
| `projects` | id, title, status, source_type=`upload`, source_url=null, source_meta, target_*, created_at, updated_at |
| `jobs` | id, project_id, kind (`pipeline`\|`export`), stage, status, progress, error, checkpoint JSON, timestamps |
| `assets` | id, project_id, kind (`raw`\|`proxy`\|`audio_wav`), path, checksum, bytes, probe JSON |
| `transcripts` | id, project_id, language, model_name, words_path, segments_path, srt_path |
| `clips` | id, project_id, rank, t_start, t_end, title, status (`accepted` for manual), crop JSON, user_t_* optional |
| `clip_exports` | id, clip_id, preset, path, status, width, height, duration_sec |

**Project status enum:** `created` → `ingesting` → `processing` → `ready_for_review` → `failed` (and `exporting`/`done` as needed).

**Filesystem layout:**

```text
data/projects/{project_id}/
  raw/source.<ext>
  proxy/proxy.mp4
  audio/audio.wav
  meta/ffprobe.json
  transcript/words.json
  transcript/segments.json
  transcript/episode.srt
  clips/{clip_id}/
    captions.srt
    captions.ass
    export.mp4
```

---

## 4. Pipeline stages (Phase 1)

Run as one Arq job `pipeline` after upload:

| Stage | Implement? | Behavior |
| --- | --- | --- |
| `ingest` | Yes | Move/copy upload into `raw/`; checksum; probe |
| `normalize` | Yes | FFmpeg → 720p proxy + 16 kHz mono WAV + ffprobe.json |
| `asr` | Yes | faster-whisper `word_timestamps=True` → words/segments/srt + DB row |
| `segment_score` | **Skip** | No-op / not enqueued |
| `propose_windows` | **Skip** | User creates clips manually |
| `reframe` | Partial | On export: **center crop** to 9:16 |
| `captions` | Yes | On export: build ASS+SRT for `[t_start,t_end]` |
| `export` | Yes | Separate Arq job `export` per clip |

**Idempotency:** If proxy/wav/words exist and raw checksum unchanged, skip those stages. Store completed stages in `jobs.checkpoint`.

---

## 5. Tasks (complete in order)

### Task 1.1 — Expand models + migrations

**Do:** Alembic migration for full Phase 1 schema above. UUID primary keys as strings (or UUID type). Indexes on `project_id` FKs.

**Done when:** Migration upgrades cleanly on empty DB.

---

### Task 1.2 — Config additions

**Do:** Extend settings with:

| Variable | Default |
| --- | --- |
| `WHISPER_MODEL` | `medium` on CPU-friendly default; document `large-v3-turbo` if GPU/Apple |
| `WHISPER_DEVICE` | `auto` |
| `WHISPER_COMPUTE_TYPE` | `auto` / `int8` for CPU |
| `WHISPER_MODEL_DIR` | `/models/whisper` |
| `MAX_SOURCE_DURATION_SEC` | `10800` |
| `MAX_SOURCE_BYTES` | `8589934592` |
| `EXPORT_WIDTH` / `EXPORT_HEIGHT` | `1080` / `1920` |
| `CLIP_MIN_SEC` / `CLIP_MAX_SEC` | `5` / `180` (manual can be wider than auto later) |
| `ALLOW_URL_INGEST` | `false` (must stay false this phase) |

Update `.env.example`.

---

### Task 1.3 — `POST /api/v1/projects` (upload)

**Do:**
- Multipart form: `file` (required), `title` (optional).
- Validate extension/MIME roughly; enforce max bytes.
- Create project row `source_type=upload`, status `created`→`ingesting`.
- Save file under project `raw/`.
- Create `jobs` row kind=`pipeline`, enqueue Arq `run_pipeline(project_id)`.
- Return `{ id, status }` immediately (202 or 200).

**Error shape:** `{ "error": { "code": "...", "message": "..." } }`

**Done when:** Upload via curl creates project + raw file + queued job.

---

### Task 1.4 — Pipeline: ingest + normalize

**Do:** Worker stages:
1. Probe with ffprobe; reject no-audio or over duration cap.  
2. Write `meta/ffprobe.json`; create `assets` rows.  
3. FFmpeg proxy: scale to max height 720, H.264 yuv420p.  
4. FFmpeg WAV: `-ac 1 -ar 16000`.  
5. Update job `stage` + `progress` (e.g. 0.1 ingest, 0.3 normalize).

**Done when:** After job, proxy + wav exist; project not failed.

---

### Task 1.5 — Pipeline: ASR (faster-whisper)

**Do:**
- Load model from `WHISPER_MODEL_DIR` cache (download on first run).  
- Transcribe WAV with `word_timestamps=True`.  
- Write `words.json` as list of `{ "word", "start", "end", "prob"? }`.  
- Write `segments.json` with segment text + times.  
- Write `episode.srt`.  
- Insert `transcripts` row.  
- Set project status `ready_for_review`, job `succeeded`.

**Failure:** OOM/model errors → job `failed`, project `failed`, error message stored.

**Done when:** Real short sample (or fixture) produces non-empty words.json.

**Note:** Bundle or document a tiny licensed/owned `testdata/sample.mp4` if possible; otherwise manual smoke with user file.

---

### Task 1.6 — Read APIs

**Do:**
- `GET /api/v1/projects` — list recent (id, title, status, created_at).  
- `GET /api/v1/projects/{id}` — detail + latest job progress/error.  
- `GET /api/v1/projects/{id}/transcript` — words + segments (inline if small; or paths + `GET /api/v1/files/...`).  
- `GET /api/v1/projects/{id}/clips` — list clips.  
- `GET /api/v1/files/{project_id}/...` — **path-traversal safe** file serving for proxy/exports (only under that project dir).

**Done when:** UI (or curl) can poll until `ready_for_review` and fetch transcript JSON.

---

### Task 1.7 — Clip create / update / export enqueue

**Do:**
- `POST /api/v1/projects/{id}/clips` body: `{ t_start, t_end, title? }` → status `accepted`, validate duration bounds and `0 <= start < end <= media_duration`.  
- `PATCH /api/v1/clips/{id}` — update trims/title.  
- `POST /api/v1/clips/{id}/export` — create `clip_exports` row `pending`, enqueue `run_export(clip_id)`.  
- `GET /api/v1/clips/{id}/exports` — status + download URL when `ready`.

**Done when:** Full API path works without UI.

---

### Task 1.8 — Captions generator

**Do:** Module `pipeline/captions.py`:
- Input: words in absolute times, clip `t_start`/`t_end`.  
- Shift times to clip-zero.  
- Emit SRT.  
- Emit simple ASS: white text, black outline, bottom-center, font size readable on 1080×1920, ~2–4 words per cue OR one segment per cue (pick one; document).  
- Safe zone: keep captions above bottom ~12–15% of frame.

**Done when:** Unit test or script generates ASS/SRT from fixture words.

---

### Task 1.9 — Export job (FFmpeg)

**Do:** `run_export`:
1. Resolve raw (prefer) or proxy.  
2. Center crop to 9:16 then scale to 1080×1920 (handle already-vertical sources).  
3. Trim to `[t_start, t_end]` (accurate seek strategy: input seek + trim filter as appropriate).  
4. Burn ASS via `ass` or `subtitles` filter; **bundle a font** in the image or set `fontsdir` so Docker doesn’t silently miss glyphs.  
5. Audio: AAC; optional loudnorm can wait for Phase 3.  
6. Write `clips/{id}/export.mp4`; update export row `ready` with probe dims/duration.  
7. Fail clearly if FFmpeg errors (store stderr tail in job.error).

**Acceptance probe:** width=1080, height=1920, duration ≈ trim ±0.5s, has video+audio.

**Done when:** curl download yields playable vertical captioned MP4.

---

### Task 1.10 — Web UI: upload + project list

**Do:**
- Upload form (drag/drop nice-to-have).  
- Projects list with status badges.  
- Auto-poll project detail every 2–3s while processing.

**Done when:** Browser-only upload reaches `ready_for_review`.

---

### Task 1.11 — Web UI: transcript trim + export

**Do:**
- Project page: play **proxy** video (HTML5).  
- Transcript pane; clicking a word sets start or end (document UX: first click start, second end—or separate buttons).  
- Numeric inputs for start/end as fallback.  
- “Create clip” → then “Export” → show export status → Download button.  
- Show job errors visibly (no silent failure).

**Done when:** Entire §2 user flow works without curl.

---

### Task 1.12 — Worker Docker deps for media

**Do:** Ensure worker/API image has:
- `ffmpeg` + `ffprobe`  
- faster-whisper + compatible torch/ctranslate2 for CPU (GPU optional/document)  
- Enough shared memory / note on first model download into `/models/whisper`

Update Compose + README with first-run model download time/disk.

**Done when:** Fresh `compose up` can complete ASR on a short file without host-venv.

---

### Task 1.13 — Phase 1 README section + smoke

**Do:**
- Document: upload-only, local Whisper, no URL.  
- `scripts/smoke_phase1.sh` or documented curl sequence.  
- Update plans progress tracker.

**Done when:** Another agent can verify Phase 1 from docs alone.

---

## 6. API checklist (Phase 1)

| Method | Path | Required |
| --- | --- | --- |
| GET | `/api/v1/health` | yes (from P0) |
| POST | `/api/v1/projects` | multipart upload |
| GET | `/api/v1/projects` | yes |
| GET | `/api/v1/projects/{id}` | yes |
| GET | `/api/v1/projects/{id}/transcript` | yes |
| GET | `/api/v1/projects/{id}/clips` | yes |
| POST | `/api/v1/projects/{id}/clips` | yes |
| PATCH | `/api/v1/clips/{id}` | yes |
| POST | `/api/v1/clips/{id}/export` | yes |
| GET | `/api/v1/clips/{id}/exports` | yes |
| GET | `/api/v1/files/{project_id}/...` | yes |
| DELETE | `/api/v1/projects/{id}` | nice-to-have |

---

## 7. Parallelization (optional multi-agent)

**Only after Task 1.1–1.2 (schema/config) merged:**

| Agent A (pipeline) | Agent B (API+UI) |
| --- | --- |
| 1.3–1.5, 1.8–1.9, 1.12 | 1.6–1.7, 1.10–1.11 |

Single agent for final smoke (1.13) and conflict resolution on shared models.

---

## 8. Exit criteria (all required)

- [ ] Upload owned MP4 via UI.  
- [ ] ASR produces words with timestamps.  
- [ ] Manual in/out creates clip.  
- [ ] Export produces **1080×1920** MP4 with **visible burned captions**.  
- [ ] Download works from UI.  
- [ ] `ALLOW_URL_INGEST=false`; no yt-dlp dependency required to run happy path.  
- [ ] No Ollama/LLM code required for happy path.  
- [ ] Failures surface in UI (failed job message).  
- [ ] Path traversal protected on file route.  
- [ ] Progress tracker: Phase 1 = `done`.

---

## 9. Quality bar (“good enough”)

- Cuts may be imperfect; user can adjust times.  
- Center crop may chop heads on wide shots—acceptable until Phase 3.  
- Caption typos from Whisper acceptable if user can re-trim; override editor is Phase 3.  
- You would personally use this on **your own** recordings.

---

## 10. Agent completion report

```text
Phase 1 complete.
- Demo steps: ...
- Whisper model used: ...
- Sample duration tested: ...
- Known issues: ...
- Next: docs/plans/PHASE_2_RANK_AND_URL.md
```
