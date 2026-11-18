# Cliping

Personal local tool: turn long-form video into short vertical clips with burned-in captions.

**Phase 4:** same clipping flow, plus a personal deploy lock (API token), disk cleanup, optional watermark, and an ops runbook. SQLite and local files stay the default.

## Prerequisites

- **Recommended:** Docker + Docker Compose
- **Or local:** Python 3.10+, Node 20+, Redis, FFmpeg

## Quick start (Docker)

Start **Docker Desktop**, then:

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
| --- | --- |
| Web | http://localhost:3001 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/api/v1/health |

First ASR run downloads the Whisper model into `models/whisper/` (hundreds of MB; can take several minutes).

### Local dev (without Compose)

```bash
cp .env.example .env
# use host paths, e.g.:
# DATA_DIR=./data
# DATABASE_URL=sqlite+aiosqlite:///./data/cliping.db
# REDIS_URL=redis://localhost:6379/0
# WHISPER_MODEL_DIR=./models/whisper
# FONTS_DIR=./services/api/app/assets/fonts

cd services/api && python3 -m venv .venv && source .venv/bin/activate
pip install -e .
alembic upgrade head

# terminal 1 — API
uvicorn app.main:app --reload --port 8000

# terminal 2 — worker (needs FFmpeg + Whisper model download on first job)
arq app.worker.WorkerSettings

# terminal 3 — web (CLIPING_API_URL is the server-side upstream)
cd apps/web && npm install && CLIPING_API_URL=http://localhost:8000 npm run dev
```

## Smoke

```bash
./scripts/smoke_phase0.sh
# after stack is up + you have a short owned MP4:
# ./scripts/smoke_phase1.sh /path/to/sample.mp4
# ./scripts/smoke_phase2.sh /path/to/sample.mp4
./scripts/smoke_phase3.sh
./scripts/smoke_phase4.sh
./scripts/smoke_phase5.sh                      # unit tests only
# ./scripts/smoke_phase5.sh /path/to/owned.mp4  # full autopilot run against the live stack
./scripts/smoke_phase6.sh                      # publish unit tests; status check if the API is up
```

Phase 3 checks (crop math, karaoke ASS, export hash; FFmpeg burn when `ffmpeg` is on `PATH`):

```bash
cd services/api && .venv/bin/python -m unittest tests.test_phase2 tests.test_phase3 tests.test_phase4
```

## Proposals and review

After ASR the worker scores the transcript and inserts `proposed` clips. Heuristics prefer questions, exclamations, pause boundaries, and keywords (`KEYWORD_BOOSTS`), then snap in/out points onto word boundaries. Windows aim at the project target duration (default 45s) and stay inside `CLIP_MIN_SEC` / `CLIP_MAX_SEC`.

The review page lists title, score, reason, and duration. Accept or reject before export. Export stays blocked until a clip is accepted. Manual “create clip” is still available and creates an `accepted` clip. Re-ranking replaces `proposed` rows only; `accepted`, `rejected`, and `exported` clips stay.

Re-run from a stage without repeating earlier work when artifacts are already on disk:

```bash
curl -X POST http://localhost:8000/api/v1/projects/$ID/reprocess \
  -H 'Content-Type: application/json' \
  -d '{"from_stage":"segment_score"}'
```

`from_stage` is one of `ingest`, `normalize`, `asr`, `segment_score`, `propose_windows`, `captions`, `export`. Starting at `segment_score` does not re-download or re-transcribe when `raw/`, the wav, and `words.json` are already present. `captions` and `export` only re-queue accepted clips (force rebuild, no ASR). `POST /api/v1/projects/$ID/propose` is the same re-rank from `segment_score`. Delete a project and its files with `DELETE /api/v1/projects/$ID`. Delete one clip with `DELETE /api/v1/clips/$CLIP_ID`.

Progress is on `GET /api/v1/projects/{id}` (`latest_job.stage`, `progress`, `error`). Optional stream: `GET /api/v1/projects/{id}/events`.

## Ollama (optional)

Default `LLM_PROVIDER=none` uses heuristics only. To rank with a local model:

```bash
ollama pull llama3.1:8b
```

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434   # host process
# inside Compose, the worker uses:
# OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.1:8b
```

Ollama stays on the host. If the model is down or returns invalid JSON, the pipeline keeps the heuristic proposals and still finishes. `openai_compatible` uses `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` the same way, and also falls back to heuristics. The fallback reason is stored in the job checkpoint (`segment_score.llm_error`) instead of being swallowed.

## Autopilot (paste link → rendered Shorts)

Paste a link on the home page with **Autopilot** on (the default) and a clip count (default 10). With no further clicks the worker transcribes, ranks, drafts post text, and renders every kept clip to a 1080×1920 MP4. The project page opens on a gallery: play each Short, edit its title, description, and hashtags, exclude clips you do not want, and download. The transcript, trim, crop, and caption tools stay below ("Open in editor"). Nothing is posted; posting is Phase 6.

Rules:

- Clips are 20–59 s (target 35 s), non-overlapping, and snapped to speech pauses.
- Count is `min(requested, floor(speech_seconds / 35))`. A short source makes fewer clips and the page says why. Clips are never padded to reach the number.
- Rendering a clip writes a local file only, so autopilot renders proposals without an Accept click. Manual projects still need Accept before export.
- One clip failing to render does not stop the batch. The page lists the failure.
- Your edits to post text are marked `user` and survive **Redraft post text**. Changing a clip's trim or caption text marks its draft **stale**.

API: `POST /api/v1/projects` takes `autopilot` and `clip_count` (1–30) in the URL JSON body or as multipart form fields. `GET /projects/{id}` adds `autopilot_summary` (requested, planned, rendered, failed, provider_used, llm_error, metadata_error). `GET /projects/{id}/clips` includes `post_*` fields and `latest_export`. `PATCH /clips/{id}` accepts `post_title`, `post_description` (body text; hashtags are appended), and `post_hashtags`, and returns 400 instead of silently trimming. `POST /projects/{id}/post-metadata` redrafts non-user drafts.

### Gemini (optional)

Without a key, autopilot uses heuristic ranking and fallback drafts (title = heuristic title, empty description and hashtags), and the gallery shows why. To use Gemini:

1. Create a key in [Google AI Studio](https://aistudio.google.com/apikey).
2. In `.env`:

```bash
LLM_PROVIDER=gemini              # Gemini ranks the full transcript (cap GEMINI_MAX_TRANSCRIPT_CHARS)
GEMINI_API_KEY=...               # also enables Gemini post text
GEMINI_MODEL=gemini-3.8-flash
GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite
CHANNEL_CONTEXT="Weekly cooking tips for beginners"
```

3. `docker compose up -d --build api worker`.

Quota: autopilot makes **two** Gemini calls per video (one to rank, one for all post text). Free-tier limits are per Google project and unpublished; observed numbers are about 20 requests/day on Flash, so roughly 10 videos a day. A per-minute 429 is retried once. A spent daily quota tries `GEMINI_FALLBACK_MODEL` once, then falls back to heuristics with "Gemini daily quota reached; resets at midnight Pacific" on the page. On the free tier Google may use prompts (your transcript text) to improve its products; use a paid key if that matters. Only transcript text is sent: no audio or video.

## URL ingest

Off unless `ALLOW_URL_INGEST=true`. The UI asks you to accept a rights disclaimer before enqueue. The API stores `source_meta.disclaimer_accepted_at`. Send JSON, not multipart:

```bash
curl -X POST http://localhost:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/video","disclaimer_accepted":true}'
```

With the flag false the API returns 403. Without `disclaimer_accepted: true` it returns 400. A video of 5 minutes or less is one `yt-dlp` download into `raw/`. A longer finished video, up to `MAX_SOURCE_DURATION_SEC` (12 hours), is fetched in 5-minute pieces: one process downloads the next piece while the pipeline makes shorts from the current one. The 8 GB cap applies to each piece, not to the whole video. DRM, login, and geo blocks fail that piece and tell you to upload a file you have rights to. There is no cookie or bypass path. Prefer uploading media you own.

If `yt-dlp` is installed, creating a project from a URL also probes with `yt-dlp -J --no-download` (about 20 seconds at most). An `is_live` result is refused with `USE_LIVE_INGEST` and no project is created. If that probe cannot run, the worker still rejects a live download. This form does not follow a broadcast.

### Live

Off unless `ALLOW_LIVE_INGEST=true`. A YouTube live URL also needs `ALLOW_URL_INGEST=true`. Folder mode does not. One capture at a time. The rights checkbox is required.

Clips show up while the broadcast continues, about one chunk behind (`LIVE_CHUNK_SEC` plus `LIVE_EDGE_LAG_SEC`, default 5 minutes and 20 seconds). Stop ends the capture and keeps clips already made. A broadcast that is still on air uses this Live form. A finished video, including one that used to be a livestream, uses the URL form above.

```bash
curl -X POST http://localhost:8000/api/v1/projects/live \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","disclaimer_accepted":true}'
```

Or a directory of finished files under `DATA_DIR` (or `LIVE_FOLDER_ROOT`):

```bash
curl -X POST http://localhost:8000/api/v1/projects/live \
  -H 'Content-Type: application/json' \
  -d '{"folder":"/data/recordings/show","disclaimer_accepted":true}'
```

```bash
curl -X POST http://localhost:8000/api/v1/projects/$PROJECT_ID/live/stop
```

What it will not do:

- It does not pass cookies, a login, `--download-sections`, or `--live-from-start`.
- It does not transcribe `follow.mp4.part`. Closed windows are separate files.
- It does not post anywhere. Review and export stay as they are.
- It does not change a finished-file download. `post_live`, premieres that have not started, and ordinary videos are refused on the live form.

The follow file is `data/projects/<id>/capture/follow.mp4.part` while `yt-dlp` is running. That partial is mpegts. Capture cuts it at least `LIVE_EDGE_LAG_SEC` behind the file's current duration. After a clean exit, a tail of at least `CLIP_MIN_SEC` is kept. If the capture process dies, the next API start resumes at the live edge and says the gap was not recorded.

### Live now

**Live now** is a page at `/live`. It lists a ranked sample of public broadcasts from YouTube Data API `search.list` (`eventType=live`), then checks they are still live. It is off until `YOUTUBE_API_KEY` is set. That key is free and stays on the API. **Start clipping** on a card begins a live capture for that watch URL. The rights checkbox and `ALLOW_LIVE_INGEST` still apply. Pasting a URL on the home page still works.

The default sample is cached for 45 minutes. A typed search is cached for 10 minutes. There is no second page. The screen does not claim to show every livestream on YouTube. A spent search quota returns “exhausted until midnight Pacific” and leaves paste-a-URL working.

```bash
curl http://localhost:8000/api/v1/youtube/live
curl 'http://localhost:8000/api/v1/youtube/live?q=news'
```

## Caption and crop quality

New clips default to crop mode `face`. Export samples the proxy about every 0.75s, keeps the largest face in each frame, and frames the median center. The stored crop is normalized to the source frame (`x`, `y` top-left, `w`, `h` size, all 0–1). No face sets `fallback` to `center` and uses a center window. `left`, `right`, and `center` are fixed 9:16 frames. `custom` needs `x` and `y` (and optional `w`, `h`); export fits a 9:16 window on that box.

```bash
curl -X PATCH http://localhost:8000/api/v1/clips/$CLIP_ID \
  -H 'Content-Type: application/json' \
  -d '{"crop":{"mode":"left"}}'
```

Captions are karaoke ASS: 1–4 words per page, white until the word is sung, then yellow, DejaVu Sans Bold with a black outline. On 1080×1920 the block sits 80px from the left, 200px from the right, and 420px from the bottom (clear of the top 240px). The font files are `services/api/app/assets/fonts/DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` (`FONTS_DIR` in Docker). Changing trim, crop, or caption text marks a ready export `pending`. An identical `POST /clips/{id}/export` skips the encode when `export.hash` matches. `?force=true` rebuilds.

Caption override on the clip:

- empty: transcript words inside the trim
- plain text: same timings when the word count matches, otherwise spread across the spoken span
- JSON `{"words":[{"word":"Hello","start":1.2,"end":1.5}]}` with source-timeline seconds

Export audio uses one-pass `loudnorm=I=-16:TP=-1.5:LRA=11`.

`ASR_ALIGNER=none` is the default and does not load WhisperX. Set `ASR_ALIGNER=whisperx` only after an extra install (`pip install whisperx`). That pulls PyTorch and an alignment model (about 1 GB RAM on top of Whisper). It is not in the default image. Leave it `none` on CPU boxes.

Operator checklist:

- [ ] Face stays in frame on 3 talking-head videos; a screen share falls back to center
- [ ] Captions sit in the safe zone on a phone-sized preview
- [ ] A caption typo, then re-export, shows the corrected word

## Post to YouTube

Posting is off until `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` are set. The rest of Cliping does not change.

1. In Google Cloud, create a project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen. While it is in **Testing**, add the Google account that owns the channel as a test user.
3. Create an OAuth client (Web application). Set the authorized redirect URI to exactly:

   `http://localhost:3001/api/backend/api/v1/youtube/callback`

4. Put the client id and secret in `.env` as `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`. Restart the API and the worker (`docker compose up --build`).
5. Open Cliping and click **Connect YouTube**. Sign in with the channel owner. If the Shorts channel is a Brand Account, pick that account in Google's chooser. The video is posted to the channel the token represents.
6. On a finished autopilot project, review the gallery, choose the schedule, made-for-kids, and the rights checkbox, then **Post**. The confirm sheet shows the channel, the visibility of each clip, and the exact title and description that will be sent.
7. After the first private upload succeeds, move the consent screen to **In production**. A Testing app expires the refresh token after 7 days. An In production app used only by you keeps the refresh token until you revoke it. You can click through the unverified-app warning for your own app.

The refresh token is stored at `data/secrets/youtube_token.json` (mode `0600`). It is not served by the file route and it is not written to logs. **Disconnect** revokes it and deletes the file. Restarting the API or the worker does not require connecting again.

Connect asks for `youtube.upload` and `youtube.readonly`. The upload scope can send the file. The read scope is required afterwards for `videos.list` and for the channel name. A token from an older connect that only granted upload still posts the file, then the status check fails with “insufficient authentication scopes.” Disconnect and connect again after both scopes are on the Google consent screen.

`YOUTUBE_CATEGORY_ID` defaults to `22` (People & Blogs). The first upload checks it with `videoCategories.list`. A 403 on that call keeps `22` and does not add a scope.

Spaced posting (the default) sends the first clip now and the rest as private videos with `publishAt`, every `YOUTUBE_DEFAULT_SPACING_MIN` minutes (180). YouTube makes each scheduled video public at that time. **All now** uses the visibility you pick (private by default). Subscriber notifications are off unless you check them. A clip longer than 180 seconds is refused. A clip longer than 60 seconds is posted with a warning, because a copyright claim can block a Short of that length.

Posting the same export again does not create a second video. If the worker stops mid-upload, the saved resumable session is continued on restart. A quota error (`quotaExceeded` or `uploadLimitExceeded`) fails that clip and stops the rest of the batch. The quota resets at midnight Pacific.

Manual check once credentials are set: post 2 clips as **private** (All now, visibility private), confirm they appear in YouTube Studio, then delete them.

## Post to TikTok

Posting is off until `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` are set. YouTube posting does not change.

1. Create an app at [developers.tiktok.com](https://developers.tiktok.com/).
2. Add **Login Kit** for **Desktop** and **Content Posting API** Direct Post.
3. Request scopes `user.info.basic` and `video.publish`.
4. Register this redirect as a Desktop URI, with no query string:

   `http://localhost:3001/api/backend/api/v1/tiktok/callback`

   Use port 3001. That is the host port in Compose. Port 3000 is the container's own port and will not match the browser.
5. Put the client key and secret in `.env` as `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`. Restart the API and the worker (`docker compose up --build`).
6. Open Cliping and click **Connect TikTok**. Approve the consent screen with the account that should receive the posts.
7. On a finished autopilot project, open **Post N clips to TikTok**. The sheet loads the account nickname from TikTok at that moment. Choose a privacy value (nothing is pre-selected), confirm the music line and the rights line, then post. The caption sent is the title, a newline, and the description, exactly as shown.

Until TikTok audits the app, the account must be **private** and the privacy must be **Only me**. Choosing Everyone on an unaudited app fails with TikTok's error and does not upload. TikTok may refuse the audit: their guidelines treat a personal uploader, and an app that can pull video from another platform, as unacceptable. This phase does not submit that audit, and it does not claim public posting works.

The refresh token is stored at `data/secrets/tiktok_token.json` (mode `0600`). It is not served by the file route and it is not written to logs. Access tokens last about 2 hours. Refresh tokens last about 180 days and rotate on every refresh. **Disconnect** revokes the token and deletes the file. Restarting the API or the worker does not require connecting again.

A clip longer than 180 seconds, longer than the account's `max_video_post_duration_sec`, or outside 23–60 fps is refused before upload. If `WATERMARK_TEXT` or `WATERMARK_IMAGE` is set, TikTok posting is refused until you turn the watermark off and export again. A private success says **Posted to TikTok** and has no link. A public post id, when TikTok returns one, is linked as `https://www.tiktok.com/video/{id}`; confirm that path on the first audited public post.

Manual check once credentials are set: post 2 clips as **Only me** to your own private account, confirm they appear in the TikTok app, then delete them. Then try Everyone and confirm TikTok rejects it without a second post.

## Personal deploy

Leave `API_TOKEN` empty on localhost. When it is set, the API returns 401 without `Authorization: Bearer` or `X-API-Token`. The Next server reads `CLIPING_API_TOKEN` and adds that header. Browsers call `/api/backend/...` on the web app, not the API origin. Inside Compose, `CLIPING_API_URL=http://api:8000`.

`STORAGE_BACKEND=local` is the default. `s3` is a stub and will not run the pipeline. Postgres is optional: `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build`. SQLite stays the default file.

`WATERMARK_TEXT` and `WATERMARK_IMAGE` are off unless set. Disk headroom is `MIN_FREE_BYTES` (1 GiB). Cleanup is `POST /api/v1/admin/cleanup` and a worker cron at 03:15 and 15:15.

Backup, Tailscale, token, and wipe steps are in [docs/OPS.md](docs/OPS.md). A 30-minute soak is `scripts/soak_phase4.sh` and still needs a long video you own.

## Docs

- [Architecture](docs/CLIPPING_ARCHITECTURE.md)
- [Operations](docs/OPS.md)
- [R&D](docs/CLIPPING_RND.md)
- [Phase playbooks](docs/plans/README.md)

## Phase status

- Phase 0 — skeleton
- Phase 1 — manual clipper (upload / ASR / export)
- Phase 2 — proposals, review, optional URL ingest
- Phase 3 — face crop, karaoke captions, idempotent re-export
- Phase 4 — token, storage interface, cleanup, optional watermark, ops runbook
- Phase 5 — autopilot: paste link → rendered Shorts with Gemini (or fallback) post text
- Phase 6 — YouTube connect and one-click batch publish (off until OAuth client env is set)
