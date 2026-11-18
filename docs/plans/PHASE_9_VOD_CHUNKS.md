# Phase 9 — Five-minute chunks for a finished long video

**Depends on:** Phase 8 (window files, overlap rejection, clips visible while later windows are still queued). Phase 2 URL disclaimer. Phase 5 autopilot may render each chunk when the project is created with autopilot on.  
**Estimated effort:** one spike, then about a week  
**Research:** [../VOD_CHUNK_POOL_RND.md](../VOD_CHUNK_POOL_RND.md)  
**Architecture refs:** §7 stages, §11 env, worker `max_jobs=1`, live window pipeline

---

## 0. Goal

A finished video longer than 5 minutes, from a URL or an upload, becomes shorts without a single download of the whole file. The first chunk is fetched, then the existing pipeline runs on it. A second process fetches the next chunk while that pipeline runs. Clips show up on the project page as each chunk finishes.

End state: paste a long public URL the operator has rights to, or upload a long file, and see proposals from the start of the video while later 5-minute ranges are still being fetched.

---

## 1. Out of scope

- A Python thread per chunk, or one worker process per chunk.
- More than one pipeline job at a time. A second pipeline slot is a follow-up, and only after the spike in Task 9.1 records idle CPU.
- `--force-keyframes-at-cuts` and any re-encode whose only job is to hit an exact 5-minute boundary.
- `--download-sections` on a URL that is still `is_live`. That stays Phase 8.
- Cookies, login, membership, age-gate, PO tokens, or any other bypass flag.
- Raising `MAX_SOURCE_BYTES`. The cap applies to each chunk file.
- Publish changes. A finished chunk does not post.
- Transcribing a range that has not been fully written.

---

## 2. Product constraints

- The rights checkbox stays required for a URL. Store `disclaimer_accepted_at` as URL ingest does.
- `is_live` is still refused on this path with `USE_LIVE_INGEST`.
- Videos of 300 seconds or less keep today’s one-shot download or upload. This phase starts only above that.
- `MAX_SOURCE_DURATION_SEC` (43200, 12 hours) is the longest span this scheduler will plan. Longer than that returns the existing duration error and does not start a partial plan.
- Ranges are 300 seconds, advanced by 280 seconds. The tail may be shorter. A tail under `CLIP_MIN_SEC` is not a job.
- Prefetch depth is 1. The fetcher may hold the next range only. It must not download the rest of an 11-hour video up front.
- One fetcher process and one pipeline process. Both are the current API image. The pipeline process stays `max_jobs=1`.
- Disk floor stays `MIN_FREE_BYTES`. If the next range would break it, stop scheduling, keep clips already made, and surface the error.
- After a chunk’s shorts have a ready export, delete that chunk’s source file. Do not delete the exports.
- Framing (`face` or `gameplay`) is the project setting already stored. New clips in a chunk use it.
- No cookie, login, or bypass flag on any `yt-dlp` argv this phase adds. `--download-sections` is allowed only on this finished-video path.

---

## 3. Pipeline shape

```text
probe (duration, live_status) → plan ranges
        └─ fetcher process: one range → chunk file
                └─ pipeline process: ingest → normalize → asr → segment_score → propose_windows
                        └─ autopilot: post_metadata → render_clips
        └─ when a fetch finishes, enqueue the next range if one remains
```

The fetcher is not the pipeline job. A chunk file is a normal pipeline input, the same way a live window is. Clip `t_start` / `t_end` are inside that file. `stream_offset_sec` is the range start on the original video.

Project status while ranges remain: `processing`. The gallery must still load, the same way `live` already does. When the last chunk finishes, status becomes `ready_for_review` if any clip exists, otherwise `done`.

---

## 4. Tasks

### Task 9.1 — Spike, before any route

On a finished video the operator has rights to (the 17 Nov probe target is acceptable: `was_live`, 41601 s):

1. `yt-dlp -J --no-download` reports a finite duration and `live_status` of `was_live` or `not_live`. It writes no media.
2. One command with `--download-sections` for a 300-second range in the middle, format `bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b`, writes one file with audio and a duration within about 15 seconds of 300. The file is far smaller than the full 41.7 GB stream. Record bytes and wall time.
3. A second section command can start while the first file is still in normalize. Record that both processes were alive together.
4. Note whether the picture starts on a keyframe before the requested time. Do not add `--force-keyframes-at-cuts` to “fix” that.

**Done when:** those four notes are written under **Spike result** at the bottom of this doc, including the `yt-dlp` version. If (2) fails with HTTP 403, record the stderr tail and stop. Do not add extractor args, cookies, or a different client. The rest of this phase waits on a format that fetches without that. If (2) only works for some formats, the fetcher uses the format that worked.

### Task 9.2 — Range plan

**Do:**

- Pure function: given duration, chunk 300, step 280, return `(start, end)` pairs. Last end is the duration. Drop a tail shorter than `CLIP_MIN_SEC`.
- Refuse a duration above `MAX_SOURCE_DURATION_SEC`.
- Unit tests for a 41601-second input (count and the last pair), a 300-second input (one range, and the caller will not use this path), and a 10-second tail.

**Done when:** the tests pass without network or ffmpeg.

### Task 9.3 — Fetcher

**Do:**

- A job that downloads one planned range to `data/projects/{id}/chunks/{index}.mp4` (URL) or FFmpeg-copies that range from the uploaded file (`-ss` before `-i` is not required; copy, do not re-encode).
- URL argv is the public-URL argv plus `--download-sections *START-END` and the format from the spike. No `--match-filter` on the whole-video duration. No `--force-keyframes-at-cuts`.
- On failure, mark that range failed, keep earlier clips, and do not delete the project.
- On success, enqueue `run_pipeline` for that chunk and, if prefetch depth allows, enqueue the next range.

**Done when:** two successive ranges land as two files, and the second fetch is queued before the first pipeline job has finished.

### Task 9.4 — Pipeline process stays single

**Do:**

- Compose runs a second service, same image as the worker, whose Arq functions are only the fetcher. The existing worker does not run the fetcher.
- The pipeline worker stays `max_jobs=1`.
- Do not raise `VOD_PIPELINE_SLOTS`. Do not add a thread pool.

**Done when:** `docker compose ps` shows both processes, and a pipeline job and a fetch job can be running at the same time.

### Task 9.5 — Wire create

**Do:**

- URL create: after the existing live probe, if duration is known and greater than 300 and not above the 12-hour cap, plan ranges and enqueue the first fetch instead of `download_url`.
- Upload create: if the probed file is longer than 300 seconds, plan ranges off the local file instead of one ingest of the whole file.
- Store `framing` as today. Chunk clips use `crop_for_framing`.
- Reuse window overlap rejection so the 20-second overlap does not produce two copies of the same moment.

**Done when:** a short URL still uses the one-shot download, and a long URL does not call `download_url`.

### Task 9.6 — Project page

**Do:**

- While later ranges are queued, the gallery and the live-style progress line stay visible. Show ranges saved and ranges failed.
- Delete a chunk source only after that chunk’s exports are ready.

**Done when:** the page shows clips from chunk 0 while a later fetch is still running.

### Task 9.7 — Docs

**Do:**

- README: long finished videos are fetched in 5-minute ranges; the 12-hour cap is the longest plan, not a single file; the 8 GB cap is per chunk.
- Note that an active broadcast still uses Live.

**Done when:** an operator can tell Link, Live, and this path apart.

---

## 5. Exit criteria

- A URL of 300 seconds or less still downloads as one file.
- An `is_live` URL still returns `USE_LIVE_INGEST` and does not start a chunk plan.
- A finished video longer than 5 minutes and at most 12 hours produces a first chunk file without writing the rest of the video.
- The pipeline for chunk 0 runs while the fetcher is writing a later chunk.
- Proposals from chunk 0 are visible before the last chunk exists.
- Overlapping proposals on the 20-second boundary are rejected once.
- A failed range does not delete earlier clips.
- No new `yt-dlp` argv contains a cookie, login, or bypass flag.
- Upload projects under 5 minutes behave as they did before this phase.

---

## 6. Parallelization

Do Task 9.1 to completion before any other task. After the spike, 9.2 is pure and can land with 9.3. 9.4 is the same owner as 9.3. UI waits on 9.5.

---

## 7. Agent completion report

```text
Phase 9 in progress
Spike result: was_live, 41601s, yt-dlp 2026.10.26. Section file 146,735,559 bytes, duration 300.023s, 163s. No HTTP 403.
Section download passed: yes, format bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b
Fetcher overlapped the pipeline: yes in the spike (normalize PID 25, next section PID 27). A 6-minute upload then saved both chunk files while the pipeline was still on the first.
Exit criteria: range tests, section argv, short URL stays one-shot, over 12h refused, is_live refused, chunk board visible while processing.
Not done: an 11-hour URL was not started. Proposals from the first chunk were not on screen yet; the smoke upload was still transcribing and was removed so the worker is free.
```

---

## Spike result

Run 17 Nov 2026 inside the API image. Target `https://www.youtube.com/live/GpIWRLQBTvU`. yt-dlp `2026.10.26`.

1. `yt-dlp -J --no-download`: `live_status=was_live`, `is_live=false`, duration `41601`. No media file.
2. `--download-sections "*20000-20300"` with `bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b` exited 0. File `146,735,559` bytes (about 140 MB, not the 41.7 GB full stream). ffprobe duration `300.023` s, `start_time=0`, audio `aac`. Wall time 163 s. No HTTP 403. The delivered file is not several seconds early, so `--force-keyframes-at-cuts` was not added.
3. libx264 normalize of that file (PID 25) was still running when the next section `*20300-20600` started (PID 27). Both were alive together. The second section also exited 0 (`107,051,371` bytes, 164 s). The normalize was then stopped.
