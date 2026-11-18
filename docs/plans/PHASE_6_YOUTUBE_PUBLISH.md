# Phase 6 — YouTube Publish (one click, whole batch)

**Depends on:** Phase 5 complete  
**Estimated effort:** ~1 week  
**Research:** [../YOUTUBE_PUBLISH_RND.md](../YOUTUBE_PUBLISH_RND.md) (API, OAuth, quota, failure codes), [../AUTOPILOT_RND.md](../AUTOPILOT_RND.md) (flow, policy)  
**Architecture refs:** §11 env/secrets, §17 security  

---

## 0. Goal

Connect your YouTube channel once. On a finished autopilot project, review the gallery, then press **one button** to post every selected clip as a Short to that channel, either all now or spaced on a schedule. Each card then shows its YouTube link and status.

End state: gallery → Post N clips → confirm → N videos on the channel (private, scheduled, or public as chosen), no duplicates, clear errors.

---

## 1. Out of scope

- Posting without a click (on export or pipeline completion).
- TikTok, Instagram, other platforms; a calendar view.
- Thumbnails, playlists, comments, sidecar captions, analytics.
- More than one connected channel at a time.
- Multi-user OAuth verification or a YouTube quota-extension audit.

---

## 2. Policy constraints (must implement)

From the YouTube API Services Developer Policies and Terms (see research docs):

- The publish screen names the **connected channel**.
- It **displays the visibility** each video will get (public / unlisted / private / scheduled).
- Uploads happen only after an explicit click on that screen; that click is the consent.
- The uploaded title and description are **exactly** the text shown. Snapshot them into the publish row at click time. Never append or alter text afterward.
- The title field allows the full 100 characters.
- Made-for-kids is chosen by the operator (default remembered after first choice), never inferred.
- Rights checkbox required per batch: “I own these videos or have permission to post them.”
- `notifySubscribers` defaults to off.

---

## 3. Flow

```text
Connect YouTube (once) → gallery → Post N clips → confirm sheet → publish rows (snapshot) → Arq run_publish per row
  → resumable videos.insert → store video id → poll processing → scheduled / live / failed
```

---

## 4. Tasks

### Task 6.1 — OAuth connect

**Do:**
- Deps: `google-api-python-client`, `google-auth-oauthlib` (and what they need). Same in Docker as declared.
- Module `app/youtube/oauth.py`: build the consent URL (`youtube.upload` scope, `access_type=offline`, `prompt=consent`, random `state` stored server-side with expiry), exchange the code, refresh tokens.
- Routes: `GET /api/v1/youtube/status` (connected?, channel title if available), `GET /api/v1/youtube/connect` (redirect to Google), `GET /api/v1/youtube/callback` (validate `state`, store token, redirect to the web app), `POST /api/v1/youtube/disconnect` (revoke + delete).
- Redirect URI through the web proxy so `API_TOKEN` still applies: `http://localhost:3000/api/backend/api/v1/youtube/callback` (configurable).
- Token file at `DATA_DIR/secrets/youtube_token.json`, mode 0600, never served by `/files`, never logged.
- Try `channels.list?mine=true` for the channel name. If `youtube.upload` alone does not allow it, show “Connected (channel name unavailable)” rather than widening scope silently; record the result in the README.

**Done when:** Connect → Google consent → back in Cliping showing connected; restart API and worker → still connected; Disconnect works.

---

### Task 6.2 — Publish model and service

**Do:**
- Table `youtube_publishes`: `id`, `project_id`, `clip_id`, `export_id`, `export_hash`, `title`, `description`, `privacy_status`, `publish_at`, `notify_subscribers`, `made_for_kids`, `contains_synthetic_media`, `status` (`queued`, `uploading`, `processing`, `scheduled`, `live`, `failed`), `youtube_video_id`, `session_uri`, `error_code`, `error_message`, `created_at`, `finished_at`.
- `POST /api/v1/projects/{id}/publish` body: `clip_ids[]`, `privacy` (`public` | `unlisted` | `private`), optional `schedule {start_at, every_minutes}`, `notify_subscribers`, `made_for_kids`, `rights_confirmed: true`.
- Validate: connected; rights confirmed; each clip has a **ready** export and a non-empty `post_title`; export duration ≤ 180 s (warn in the response if > 60 s); title/description validators from Phase 5.
- Pure function `schedule_times(start_at, every_minutes, n) -> list[datetime]`. Scheduled rows use `privacy_status=private` + `publish_at`. A time in the past publishes immediately; reject times more than a year out.
- Idempotency: if a row for the same `export_hash` is already `scheduled`/`live`/`processing`, return it instead of creating another. Disable the button while a batch is running.
- Enqueue `run_publish(publish_id)` per row; the worker runs them one at a time.
- `GET /api/v1/projects/{id}/publishes` for status.

**Done when:** Posting the same project twice creates no second set of rows.

---

### Task 6.3 — Upload worker

**Do:**
- Module `app/youtube/upload.py` and worker function `run_publish`.
- If `youtube_video_id` is already stored: skip insert, go to polling.
- If `session_uri` is stored: resume it (query the uploaded range first). Else start a resumable `videos.insert` with `part=snippet,status`, save the session URI **before** sending bytes, then upload `export.mp4` unchanged.
- Body: `snippet.title`, `snippet.description`, `snippet.categoryId` (env default, validated once), `snippet.defaultLanguage` when known; `status.privacyStatus`, `status.publishAt`, `status.selfDeclaredMadeForKids`, `status.containsSyntheticMedia=false` by default; query `notifySubscribers`.
- Store the video id the moment the insert returns.
- Poll `videos.list?part=status,processingDetails` with backoff (a handful of calls, not a tight loop). Map to `scheduled` / `live` / `failed`; store `uploadStatus`, `failureReason`, `rejectionReason`, `processingFailureReason` as `error_code`.
- 5xx / network: exponential backoff and resume the same session. Per-day quota (`quotaExceeded`) or `uploadLimitExceeded`: fail this row and **stop the rest of the batch** with a clear message (“resets at midnight Pacific”). `duplicate` / `copyright` rejections: fail, never re-upload.
- Log publish id, video id, error code. Never log tokens or the session URI.

**Done when:** Killing the worker mid-upload and restarting produces one video, not two.

---

### Task 6.4 — UI

**Do:**
- Header pill: YouTube connected (channel name) / Connect button.
- Gallery cards: checkbox (default checked for non-excluded clips with a ready export), publish status chip, YouTube link when available, error text on failure, “> 60 s” warning badge.
- Publish bar: count, visibility select, schedule (default **spaced**: first now, every 3 hours; or “all now”), notify subscribers (off), made for kids (required choice), rights checkbox (required), **Post N clips** button.
- Confirm sheet before sending: channel name, visibility/schedule per clip, total count, and a reminder that titles and descriptions are sent exactly as shown.
- Poll publish status every 2–3 s while any row is active.

**Done when:** Browser-only: connect → post 10 → watch statuses → open a link.

---

### Task 6.5 — Docs, tests, smoke

**Do:**
- README: Google Cloud project, enable YouTube Data API v3, OAuth consent screen (add yourself as test user; move to In production after the first private upload so the refresh token does not expire in 7 days), client id/secret in `.env`, redirect URI.
- Unit tests (pure logic only): `schedule_times`, publish request validation, “sent text equals snapshot text,” quota/rejection error classification.
- Manual smoke: post 2 clips as **private** to your own channel, confirm they appear in YouTube Studio as Shorts, then delete them.
- Update the plans tracker and [../CLIPPING_ARCHITECTURE.md](../CLIPPING_ARCHITECTURE.md) decision log.

**Done when:** Docs let a fresh operator connect and post without reading code.

---

## 5. Env vars (Phase 6 additions)

| Variable | Default | Notes |
| --- | --- | --- |
| `YOUTUBE_CLIENT_ID` | empty | Feature off when empty |
| `YOUTUBE_CLIENT_SECRET` | empty | Never logged |
| `YOUTUBE_REDIRECT_URI` | `http://localhost:3000/api/backend/api/v1/youtube/callback` | Must match the Google Cloud client |
| `YOUTUBE_CATEGORY_ID` | `22` | Validated with `videoCategories.list` once |
| `YOUTUBE_DEFAULT_SPACING_MIN` | `180` | Schedule spacing default |

---

## 6. Parallelization

| Agent A (backend) | Agent B (frontend) |
| --- | --- |
| 6.1 OAuth, 6.2 model/service, 6.3 worker | 6.4 UI against the 6.2 contract |

Land the publish request/response shapes from 6.2 first.

---

## 7. Exit criteria

- [ ] Connect once; survives API/worker restart.
- [ ] One click posts 10 clips from an autopilot project; each row ends `scheduled` or `live` with a working link.
- [ ] Uploaded titles and descriptions match the gallery text byte for byte.
- [ ] Spaced schedule produces the expected `publishAt` times.
- [ ] Second click on the same batch creates no duplicates; worker restart mid-upload creates no duplicate.
- [ ] Quota exhaustion and a disconnected token show clear errors and stop the batch.
- [ ] Videos ≤ 180 s and vertical show up as Shorts in YouTube Studio.
- [ ] Projects without YouTube credentials work exactly as in Phase 5.
- [ ] Progress tracker: Phase 6 = `done`.

---

## 8. Agent completion report

```text
Phase 6 complete.
- Channel used (own?): …
- Clips posted / privacy used: …
- Duplicate-safety verified (double click, worker kill): yes/no
- channels.list under youtube.upload: works / 403
- Refresh token survives restart: yes/no
```
