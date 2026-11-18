# Phase 8 — Clips from a livestream that is still on air

**Depends on:** Phase 2 (URL disclaimer, proposals, review gallery). Phase 5 autopilot may render each window when the project is created with autopilot on. Phase 6 publish is unchanged and is not required. Phase 7 is out of this work.  
**Estimated effort:** a short spike, then about a week  
**Research:** [../LIVESTREAM_CLIPS_RND.md](../LIVESTREAM_CLIPS_RND.md)  
**Architecture refs:** §1 ingest policy, §7.1 ingest, §11 env, worker `max_jobs=1`

---

## 0. Goal

Point Cliping at a YouTube broadcast that is still live, or at a folder of files your recorder has already finished, and get reviewable vertical clips **while that broadcast continues**.

End state: start a live project → within roughly one chunk plus Whisper, proposals appear → accept and export as today → Stop ends the capture and keeps the clips already made. A finished VOD still uses the Phase 2 URL form.

---

## 1. Out of scope

- TikTok, Instagram, and any publish change.
- Posting because a chunk finished.
- `--download-sections` on an active broadcast.
- Cookies, login, membership, age-gate, or DRM bypass.
- Downloading the media through the YouTube Data API or Live Streaming API.
- Twitch, Kick, or a new RTMP server inside this app.
- Catch-up of an entire multi-hour DVR as the default.
- Transcribing the file `yt-dlp` is still appending to.
- Changing the finished-file URL path, except to refuse an `is_live` URL with a message that points at this flow.
- Live chat as a gate on which windows get transcribed. That is research only: [../LIVE_CHAT_SPIKE_RND.md](../LIVE_CHAT_SPIKE_RND.md). This phase still cuts every closed window.

---

## 2. Product constraints

- `ALLOW_URL_INGEST` must already be true for a YouTube live URL. `ALLOW_LIVE_INGEST` defaults to **false**. If it is false, the live route returns 403.
- The rights checkbox is required. Store `disclaimer_accepted_at` as URL ingest does.
- One live capture at a time on this box. A second start returns 409.
- Stay behind the live edge. Default chunk 300 s, overlap 20 s, lag 20 s.
- Each window is its own file and must pass the existing duration and size caps. The growing capture is not a `raw` asset and is not sent to ASR.
- Disk floor stays `MIN_FREE_BYTES`. If a new window would break it, stop capture, keep clips, and surface the error.
- Human review stays. Autopilot may render, under the Phase 5 rules, because rendering only writes a local file.
- No cookie flags on any `yt-dlp` argv this phase adds.

---

## 3. Pipeline shape

```text
probe → capture (long-lived, own process)
           └─ each closed window → ingest → normalize → asr → segment_score → propose_windows
                                    └─ autopilot: post_metadata → render_clips
```

The capture process is not an Arq pipeline job. Closed windows are normal pipeline jobs, one at a time, on the existing worker. Clip `t_start` / `t_end` are inside that window’s file. `stream_offset_sec` is the window’s start on the broadcast, so the gallery can show broadcast time.

Project status while capture is running: `live`. That status must load the gallery. It must not be treated as the `processing` flag that hides clips. When capture stops cleanly, status becomes `ready_for_review` if any clip exists, otherwise `done` with a clear “no windows” message. A failed probe never creates a half-started follow.

---

## 4. Tasks

### Task 8.1 — Spike, before any route

On a stream the operator has rights to, or on a local file that is still growing:

1. `yt-dlp -J` shows `live_status=is_live` and does not write media.
2. `yt-dlp` with no `--download-sections`, from the current edge, writes a growing mpegts.
3. While that process is still running, FFmpeg `-ss` + `-c copy` cuts a window that ends at least 20 s before the file’s current duration. The cut has audio and a finite duration.
4. One interrupt of `yt-dlp` leaves that cut on disk.

**Done when:** those four notes are written at the bottom of this doc under **Spike result**, including the `yt-dlp` version. If (2) or (3) fails, the YouTube-URL capture tasks below are skipped and only the local-folder source is implemented. Do not switch the design to `--download-sections`.

### Task 8.2 — Settings and probe

**Do:**

- Env: `ALLOW_LIVE_INGEST` (false), `LIVE_CHUNK_SEC` (300), `LIVE_CHUNK_OVERLAP_SEC` (20), `LIVE_EDGE_LAG_SEC` (20), `LIVE_MAX_HOURS` (8).
- `probe_live(url)` runs `yt-dlp -J --no-download` with the same “no cookies, no bypass” rule as `yt_dlp_argv`. Timeout 60 s.
- Accept only `live_status == is_live`.
- `is_upcoming`: 400, “This premiere has not started.”
- `post_live`: 400, “YouTube is still turning this into a video. Wait, then use the normal URL form.”
- `was_live` or `not_live`: 400, “This is not live. Use the normal URL form.”
- Missing binary, DRM, login, or a non-zero probe: the existing ingest failure copy, pointing at upload.

**Done when:** a unit test of the status mapping covers those five strings without calling the network.

### Task 8.3 — Data

**Do:**

- `projects.source_type` gains `live`.
- `source_meta` stores `live_status` at probe time, `capture_mode` (`youtube` or `folder`), and the disclaimer timestamp.
- New `live_windows` rows: `project_id`, `index`, `stream_start_sec`, `stream_end_sec`, `path`, `status` (`queued` / `running` / `ready` / `failed`), `error`.
- `clips.stream_offset_sec` nullable. Null on upload and URL projects.
- Alembic revision. Existing projects keep working.

**Done when:** migration upgrades a Phase 2 database.

### Task 8.4 — Capture process

**Do:** one supervisor process, started by the API, not by the pipeline worker.

YouTube mode:

- argv is the current public-URL argv **without** the duration match-filter and **without** `--merge-output-format`, plus the default live behavior (from the current edge). Output template `data/<id>/capture/follow.mp4`. While the process is alive the bytes are in `follow.mp4.part`. On yt-dlp 2026.10.26 that partial is an mpegts (ffmpeg’s live HLS mux), even when `--hls-use-mpegts` is set. Probe and cut `follow.mp4.part`. Do not wait for the rename to `follow.mp4`.
- No cookies, no `--live-from-start` unless the spike recorded a separate bounded-backlog result. Default off.
- Every time the file duration (ffprobe or a growing-read that does not seek into the tail) reaches `last_cut + LIVE_CHUNK_SEC + LIVE_EDGE_LAG_SEC`, copy `[last_cut, last_cut + LIVE_CHUNK_SEC]` to `capture/windows/NNNN.mp4` and enqueue that window. Advance `last_cut` by `LIVE_CHUNK_SEC - LIVE_CHUNK_OVERLAP_SEC`.
- Do not run FFmpeg against a range that reaches the current end of `follow.mp4.part`.
- Stop on: operator stop, `yt-dlp` exit, file not growing for 3 minutes, `LIVE_MAX_HOURS`, or low disk. After a clean `yt-dlp` exit, cut the remaining finalized media if it is at least `CLIP_MIN_SEC`.
- On supervisor crash, a restart continues from the last `ready` or `queued` window index and does not re-cut those ranges. It may start a new follow at the live edge; say that in the UI (“capture resumed at the live edge; the gap while the box was down was not recorded”).

Folder mode:

- Watch a directory path the operator chose under `DATA_DIR` or an explicit allowed root. A file is a window when its size is unchanged for 10 seconds and its suffix is a known video type. Ignore files still growing.
- Same enqueue path. `stream_start_sec` is the sum of previous window durations.

**Done when:** a growing local mpegts (the spike fixture is enough if no live URL is available) produces two closed files and a third tail on process exit, and the follow file is never passed to `run_ingest`.

### Task 8.5 — Window pipeline

**Do:** enqueue the existing `run_pipeline` for that window’s file as the project raw for that job only. Do not point `find_raw` at `follow.mp4.part`.

- Persist `stream_offset_sec` on each inserted clip.
- Re-running proposals for a window replaces only `proposed` clips for that window, same rule as Phase 2.
- Two proposals whose broadcast intervals overlap by more than half the shorter one: keep the higher score, mark the other rejected with reason `overlap`.
- A failed window stores `error` and the capture continues.

**Done when:** two overlapping fixture windows yield one surviving proposal in the overlap, and a failed window does not delete the other window’s clips.

### Task 8.6 — API

**Do:**

- `POST /api/v1/projects/live` with `url` or `folder`, plus `disclaimer_accepted: true`. Optional `autopilot` and `clip_count`, same meaning as Phase 5.
- `POST /api/v1/projects/{id}/live/stop`.
- Project payload includes `live`: `{ "mode", "state", "behind_sec", "windows_ready", "windows_failed" }`.
- Finished-file URL create: if a probe is cheap to add, refuse `is_live` with code `USE_LIVE_INGEST`. If the probe is too slow to put on the URL path, skip it and document that a live URL on the old form still fails in the worker. Prefer the refuse-on-create behavior.

**Done when:** the disabled flag returns 403, a missing disclaimer returns 400, and a second live start returns 409.

### Task 8.7 — UI

**Do:** a Live entry next to the URL form. Same disclaimer, plus one sentence that clips show up while the stream continues, about one chunk behind. Project page polls as it does now, loads clips when status is `live`, and shows state, windows ready, and Stop. Gallery rows show broadcast time from `stream_offset_sec`.

**Done when:** a fixture project in status `live` with two clips renders both without waiting for `ready_for_review`.

### Task 8.8 — Docs

**Do:** README URL section gains a short “Live” subsection. `.env.example` gains the new keys, default off. Architecture §7.1 stays the finished-file ingest; do not pretend URL ingest grew a follow loop.

**Done when:** an operator can see how to turn the feature on and what it will not do.

---

## 5. Exit criteria

- With `ALLOW_LIVE_INGEST=false`, `POST /projects/live` returns 403.
- A tested status map rejects upcoming, post-live, and ordinary videos with the messages in Task 8.2.
- A growing file produces closed windows that go through ASR and show up as proposals while the writer is still appending.
- Stop leaves those proposals on disk and does not delete them when the follow process exits.
- Upload projects and ordinary URL projects behave as they did before this phase.
- No `yt-dlp` invocation in the live path passes a cookie, login, or bypass flag.

---

## 6. Parallelization

Do Task 8.1 to completion before any other task. After the spike, 8.2 and 8.3 are one owner. UI waits on the API fields in 8.6.

---

## 7. Agent completion report

```text
Phase 8 complete
Spike result: yt-dlp 2026.10.26, cut-while-growing passed yes
YouTube URL capture: shipped
Folder capture: shipped yes
Exit criteria: pass
Not done:
```

Checked on 16 Nov 2026. The YouTube follow from the spike was not downloaded again.

- `ALLOW_LIVE_INGEST` stays false. `POST /api/v1/projects/live` returns 403 `LIVE_INGEST_DISABLED`. The home page Live form shows that message.
- A flushed growing mpegts produced two closed window files while the writer was still running, then a tail after it exited. Those paths are not the follow file.
- Two of those windows went through ingest, normalize, ASR, scoring, and proposals while the writer was still appending. The project stayed `live`. One lower-scoring overlap was marked rejected. Stop on a fixture project left both proposals in the API and moved the project to `ready_for_review`.
- The project page rendered both clips, with broadcast times, while status was `live`.
- Existing upload and URL projects still list. Phase 2 through Phase 6 unit tests passed. Live argv tests still reject cookies, login, `--download-sections`, and `--live-from-start`.

---

## Spike result

Run 16 November 2026 inside the API container (`yt-dlp` 2026.10.26, FFmpeg 7.1.5). The host has neither binary. The captured media was deleted after the probes. YouTube URL capture is **not** skipped.

1. **Probe.** `yt-dlp -J --no-download` on `https://www.youtube.com/@NASA/live` returned `live_status=is_live`, `is_live=true`, `duration=null` (watch id `ROboUHfPUrU`). No media file was written.
2. **Follow.** `yt-dlp --no-live-from-start -f bv*+ba/b --hls-use-mpegts -o follow.%(ext)s` on that watch URL selected formats `311+234` and grew `follow.mp4.part`. The ffmpeg log says the container is mpegts (`Output #0, mpegts`). The filename does not end in `.ts`.
3. **Cut while the follow was still running.** ffprobe on `follow.mp4.part` reported duration `132.916 s`, H.264 and AAC. `ffmpeg -ss 104.916 -i follow.mp4.part -t 8 -c copy ytcut.mp4` produced an 8.013 s file with H.264 and AAC. The window ended 20 s before the probed end.
4. **Interrupt.** SIGINT to that `yt-dlp` process left `ytcut.mp4` on disk. A later ffprobe still reported 8.013 s, H.264 and AAC.

A local control (FFmpeg `testsrc` appending an mpegts, cut at 32.9 s, then SIGINT) also left an 8.164 s H.264+AAC file. Same cutter, no YouTube involved.

`--live-from-start` was not tested. Keep it off.
