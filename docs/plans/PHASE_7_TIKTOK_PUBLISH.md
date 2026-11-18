# Phase 7 — TikTok Publish (one click, whole batch)

**Depends on:** Phase 5 (ready exports and saved post text). Does **not** depend on Phase 6. Do not edit the YouTube modules, tables, or button in this phase. Do not start this phase in the same run as Phase 6.  
**Estimated effort:** ~1 week for the private-post path. The TikTok audit that unlocks public posts is a separate manual submission and is not an exit criterion.  
**Research:** [../TIKTOK_PUBLISH_RND.md](../TIKTOK_PUBLISH_RND.md)  
**Architecture refs:** §11 env/secrets, §17 security. YouTube publish stays the Phase 6 path.

---

## 0. Goal

Research is closed. Start at Task 7.1. The corrected facts are in [../TIKTOK_PUBLISH_RND.md](../TIKTOK_PUBLISH_RND.md): access tokens last 2 hours, refresh tokens last 180 days and rotate on every refresh, and the Desktop redirect is port **3001**.

Connect one TikTok account. On a finished project, review the gallery, then press **one button** to post every selected clip to that account. Each card then shows posted or failed. The YouTube bar, if it is on, stays a different button.

End state: gallery → Post N clips to TikTok → confirm → N private posts on the connected account, no duplicates, clear errors. Public posting works only after TikTok audits the app. Until then, Only me on a private account is the success case.

---

## 1. Out of scope

- Any change to YouTube OAuth, `youtube_publishes`, `run_publish`, or the YouTube publish bar.
- Posting without a click (on export or pipeline completion).
- Inbox drafts (`video.upload`), photo posts, `PULL_FROM_URL`, webhooks, analytics, comments, a cover-frame picker.
- A TikTok-native schedule. The API has no `publishAt`. This phase posts now. A later slice may delay the init locally; that still requires the machine to stay on, and it is not a scheduled TikTok post.
- More than one connected TikTok account.
- Submitting or passing TikTok’s app audit.
- Re-encoding the export to fix frame rate or to strip a watermark.

---

## 2. Policy constraints (must implement)

From TikTok’s Content Sharing Guidelines and the Direct Post reference (see the research doc):

- The post sheet shows the **creator nickname** from a `creator_info/query` call made while the sheet is opening, not a name cached at connect time.
- Privacy options are exactly `privacy_level_options` from that call. The dropdown has **no default**. The operator selects one.
- Comment, duet, and stitch start unchecked. If creator info says one is disabled, that control is greyed and the body sends the matching `disable_*` as true.
- Commercial disclosure starts off. If the operator turns it on, they must pick “Your brand,” “Branded content,” or both, or Post stays disabled. Branded content cannot be combined with Only me.
- The declaration on the button is: “By posting, you agree to TikTok's Music Usage Confirmation.” When branded content is selected, use TikTok’s longer sentence that also names the Branded Content Policy.
- Nothing is sent until that click. The click is the consent.
- The caption sent is **exactly** the caption shown. Snapshot it onto the row at click time.
- If creator info says the account cannot post more right now, do not start the batch.
- Reject a clip longer than `max_video_post_duration_sec` before init.
- Do not post an export that was rendered with `WATERMARK_TEXT` or `WATERMARK_IMAGE`. Ask for a clean re-export.
- Rights checkbox, same meaning as YouTube: “I own these videos or have permission to post them.”
- After the click, tell the operator it can take a few minutes before the post shows in the TikTok app.
- `is_aigc` defaults off. A Gemini caption does not turn it on.

---

## 3. Flow

```text
Connect TikTok (once) → gallery → TikTok sheet (fresh creator info) → Post N clips
  → tiktok_publishes rows (caption snapshot) → Arq run_tiktok_publish per row
  → video/init FILE_UPLOAD → save publish_id → PUT export.mp4 → poll status/fetch
  → posted / failed
```

YouTube, if connected, is not part of this flow.

---

## 4. Tasks

### Task 7.1 — OAuth connect

**Do:**

- Module `app/tiktok/oauth.py`. HTTP through `httpx`. No new TikTok package.
- Desktop Login Kit. Authorize at `https://www.tiktok.com/v2/auth/authorize/` with scopes `user.info.basic,video.publish`, `response_type=code`, a random `state` stored server-side with a short expiry, and PKCE.
- PKCE: verifier 43–128 characters from the unreserved set. `code_challenge` is the **hex** SHA-256 of the verifier. `code_challenge_method=S256`. A new verifier per connect. Send `code_verifier` to the token endpoint. Do not use base64url; TikTok’s desktop guide specifies hex.
- Token endpoint: `POST https://open.tiktokapis.com/v2/oauth/token/` as `application/x-www-form-urlencoded`. Store `access_token`, `refresh_token`, `expires_in`, `refresh_expires_in`, `open_id`, and the granted scope list. The token guide updated 5 November 2026 says the access token lasts **2 hours** and the refresh token lasts **180 days**. Persist the returned `expires_in` and `refresh_expires_in`. Do not hardcode 24 hours or 365 days. Every refresh returns a different refresh token; replace the stored one.
- Routes under `/api/v1/tiktok/`: `GET /status`, `GET /connect`, `GET /callback`, `POST /disconnect`. Do not reuse `/youtube/*`.
- Redirect URI through the web proxy: `http://localhost:3001/api/backend/api/v1/tiktok/callback`. It must match the Desktop URI registered in the TikTok app, with no query string. 3001 is the host port in Compose (`3001:3000`) and the same port YouTube already uses. Do not register port 3000. The proxy already uses `redirect: "manual"` in `apps/web/src/app/api/backend/[...path]/route.ts`; the callback returns a browser redirect the same way `app/api/routes/youtube.py` does.
- Token file `DATA_DIR/secrets/tiktok_token.json`, mode `0600`, never served by `/files`, never logged.
- Disconnect revokes the token using the user-access-token guide, then deletes the file. A failed revoke still deletes the local file.
- Header status can use the display name from `user.info.basic`. The post sheet does not trust that name; it calls creator info.

**Done when:** Connect → TikTok consent → back in Cliping showing connected; restart API and worker → still connected; Disconnect removes the token.

---

### Task 7.2 — Creator info, model, and service

**Do:**

- `POST /v2/post/publish/creator_info/query/` when the sheet loads. Return nickname, username, `privacy_level_options`, the three interaction locks, and `max_video_post_duration_sec`. Surface `spam_risk_too_many_posts`, `spam_risk_user_banned_from_posting`, and `reached_active_user_cap` as a sheet error and do not enable Post.
- Table `tiktok_publishes`: `id`, `project_id`, `clip_id`, `export_id`, `export_hash`, `caption`, `privacy_level`, `disable_comment`, `disable_duet`, `disable_stitch`, `brand_content_toggle`, `brand_organic_toggle`, `is_aigc`, `status` (`queued`, `uploading`, `processing`, `posted`, `failed`), `publish_id`, `upload_url`, `tiktok_post_id`, `fail_reason`, `error_message`, `created_at`, `finished_at`.
- `POST /api/v1/projects/{id}/tiktok/publish` body: `clip_ids[]`, `privacy_level`, `allow_comment`, `allow_duet`, `allow_stitch`, `brand_organic`, `brand_content`, `is_aigc`, `made_for_kids` is **not** a TikTok field and must not be sent, `rights_confirmed: true`, `music_confirmed: true`.
- The service flips the allow flags into `disable_*` (allowed means not disabled). If creator info locked an interaction, force `disable_*=true` even if the client sent allow.
- Validate: connected; rights and music confirmed; privacy is one of the options just returned; branded content is not combined with `SELF_ONLY`; each clip has a ready export, a caption that is non-empty or explicitly empty within the 2200 limit, duration ≤ `max_video_post_duration_sec` and ≤ 180 s; watermark off; probed frame rate in 23–60 inclusive. Caption length is UTF-16 code units, not Python `len()`.
- Compose the caption from the saved `post_title` and `post_description` (title, newline, description). Snapshot that string. Do not append hashtags of our own.
- Idempotency: an `export_hash` that already has a row in `queued`, `uploading`, `processing`, or `posted` is returned as-is. No second row.
- Enqueue `run_tiktok_publish(publish_id)` per new row. The worker runs them one at a time.
- `GET /api/v1/projects/{id}/tiktok/publishes` for status. Never return `upload_url`.

**Done when:** Posting the same project twice creates no second set of rows. A privacy value outside the creator-info list is rejected with `privacy_level_option_mismatch` semantics before any TikTok init.

---

### Task 7.3 — Upload worker

**Do:**

- Module `app/tiktok/upload.py` and worker function `run_tiktok_publish`. Add it to the worker’s function list. On startup, requeue unfinished `queued` / `uploading` / `processing` rows.
- If `publish_id` is already stored: do not call init. Poll, or resume the PUT if status is still `PROCESSING_UPLOAD` and the URL is inside its hour.
- If there is no `publish_id`: `POST /v2/post/publish/video/init/` with `source=FILE_UPLOAD`. Write `publish_id` and `upload_url` **before** the first byte.
- Chunk plan, pure function, covered by unit tests. `total_chunk_count` is `floor(file size / chunk_size)`.
  - Under 5 MB: one chunk. `chunk_size` is the file size. `total_chunk_count` is 1.
  - From 5 MB through 64 MB inclusive: one chunk is legal. Use it. `chunk_size` is the file size.
  - Over 64 MB: `chunk_size` is 10 MB. The last PUT is that 10 MB plus the leftover bytes, matching TikTok’s 50,000,123-byte example (last body 10,000,123). The last body must stay ≤ 128 MB. Do not send a leftover smaller than 5 MB as its own chunk.
- `PUT` the saved URL, including its query string, with `Content-Type: video/mp4`, `Content-Length`, and `Content-Range`. Sequential. `206` continues. `201` moves the row to `processing`.
- `5xx`: retry the same chunk with backoff. `416`: read the progress TikTok reports and send the remaining bytes. `403` on an expired URL: do **not** init again; mark failed and tell the operator to check the TikTok app before a manual retry.
- Poll `POST /v2/post/publish/status/fetch/` a handful of times (delays on the order of 0, 5, 10, 20, 30 seconds). `PUBLISH_COMPLETE` → `posted`. If `publicaly_available_post_id` has an entry, store the first one. `FAILED` → store `fail_reason` and do not upload again.
- Still processing after those polls: leave `processing` and return a deferred requeue. Do not hold the worker for moderation that can take hours.
- Stop the rest of the batch (fail queued siblings, do not init them) on `spam_risk_too_many_posts`, `spam_risk_user_banned_from_posting`, `reached_active_user_cap`, `unaudited_client_can_only_post_to_private_accounts`, and a disconnected token.
- `rate_limit_exceeded`: retry the same row later. Do not start a second init.
- Media and spam fail reasons (`file_format_check_failed`, `duration_check_failed`, `frame_rate_check_failed`, `picture_size_check_failed`, `spam_risk_text`, `spam_risk`): fail that row only.
- Log `publish_id` and `fail_reason`. Never log tokens, the verifier, or `upload_url`.
- Keep init calls inside the 6-per-minute cap. One-at-a-time is the default; if a row finishes in under a second, wait before the next init.

**Done when:** Killing the worker after `publish_id` is saved and restarting does not create a second `publish_id`.

---

### Task 7.4 — UI

**Do:**

- Header pill of its own: TikTok connected / Connect TikTok. Hidden, or shown as “TikTok not configured,” when `TIKTOK_CLIENT_KEY` is empty. Do not replace the YouTube pill.
- A TikTok publish bar, separate from the YouTube bar. It loads creator info when opened.
- Controls required by TikTok: nickname, caption that will be sent, privacy select with no pre-selected option, comment/duet/stitch unchecked, commercial disclosure off, AI-generated checkbox off, music confirmation, rights confirmation, **Post N clips**.
- Cards can show a TikTok status chip and, when `tiktok_post_id` exists, a link. Private success with no id says posted, without a made-up URL.
- “> 60 s” can stay as information. Block the clip when it exceeds the creator’s max duration, 180 seconds, the frame-rate window, or when the export carries a watermark.
- Confirm sheet: nickname, privacy, the exact caption, the music sentence, the count.
- Poll TikTok publish status every 2–3 s while any row is active. YouTube polling stays as it is.
- Disable Post while a TikTok batch for this project is `queued`, `uploading`, or `processing`.

**Done when:** Browser-only, with a configured app: connect → post 2 clips as Only me → cards leave the uploading state. The YouTube button’s behavior is unchanged.

---

### Task 7.5 — Docs, tests, smoke

**Do:**

- README: create a TikTok developer app, add Login Kit as Desktop, add Content Posting API Direct Post, scopes `user.info.basic` and `video.publish`, redirect URI exactly the localhost callback above, client key and secret in `.env`. Say that the account must be private and the privacy must be Only me until an audit. Say that TikTok may refuse the audit because this is a personal uploader. Do not claim public posting works.
- Unit tests, pure logic only: chunk plan for a 4 MB file, a 20 MB file, and a file just over 64 MB; UTF-16 caption length; privacy rejected when it is not in the option list; branded content rejected with `SELF_ONLY`; “sent caption equals snapshot”; batch-stop versus row-fail classification; a second plan for an existing `publish_id` does not call for a new init.
- Manual smoke: post 2 clips as **Only me** to your own private account, confirm they appear in the TikTok app, then delete them. If the app is still unaudited, also confirm that choosing Everyone fails with the unaudited error and does not upload.
- Update the plans tracker. Add a short architecture pointer if it is not already there. Do not mark Phase 6 done from this work.

**Done when:** A fresh operator can connect and post two private clips from the README without reading the worker.

---

## 5. Env vars (Phase 7 additions)

| Variable | Default | Notes |
| --- | --- | --- |
| `TIKTOK_CLIENT_KEY` | empty | Feature off when empty. TikTok’s name for the client id. |
| `TIKTOK_CLIENT_SECRET` | empty | Never logged |
| `TIKTOK_REDIRECT_URI` | `http://localhost:3001/api/backend/api/v1/tiktok/callback` | Host port. Must match the Desktop URI in the TikTok app. No query string. |

No spacing default. This phase does not schedule.

---

## 6. Parallelization

| Agent A (backend) | Agent B (frontend) |
| --- | --- |
| 7.1 OAuth, 7.2 model/service, 7.3 worker | 7.4 UI against the 7.2 contract |

Land the publish request and response shapes from 7.2 first. Neither agent edits `app/youtube/` or the YouTube React components except to sit a second pill beside the existing one.

---

## 7. Exit criteria

- [ ] Connect once; survives API and worker restart; Disconnect deletes the local token.
- [ ] One click posts 2 clips to the operator’s own private TikTok account as Only me. Each row ends `posted`.
- [ ] The caption on TikTok matches the sheet.
- [ ] A second click does not create a second post. A worker restart after `publish_id` is saved does not create a second `publish_id`.
- [ ] An unaudited app refuses a non-private privacy with TikTok’s error and stops the batch.
- [ ] Daily-cap and disconnected-token errors stop the batch and say why.
- [ ] A watermarked export and a clip outside 23–60 fps are refused before init.
- [ ] Projects with no TikTok key behave as they do today. YouTube publish still behaves as it did before this phase.
- [ ] Progress tracker: Phase 7 = `done` only after the private smoke. The audit is not required for that mark.

---

## 8. Agent completion report

```text
Phase 7 partial. Code and unit tests are in. The private smoke is not.
- TikTok account used (own, private?): not run. No client key in this environment.
- Clips posted / privacy used: none
- Duplicate-safety verified (double click, worker kill after publish_id): unit test only (a stored publish_id returns resume or poll, never init; an active export_hash is reused)
- Unaudited Everyone attempt blocked: not run against TikTok
- Refresh token survives restart: not run
- YouTube flow left unchanged: yes. No edits under app/youtube/ or the YouTube React components.
```
