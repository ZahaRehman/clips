# Processing speed — why a 15-minute video takes about an hour

**Workspace:** `/Users/apple/Desktop/Cliping`
**Research date:** 18 November 2026
**Question:** Why does a 15-minute video (and a live stream) take about an hour to turn into shorts, and what would make it faster?
**Status:** Research only. No code was changed. All numbers come from this machine's database, worker container, and a benchmark run inside the worker container on an existing chunk.

Related: [VOD_CHUNK_POOL_RND.md](./VOD_CHUNK_POOL_RND.md) (chunk design), [LIVESTREAM_CLIPS_RND.md](./LIVESTREAM_CLIPS_RND.md) (live follow).

---

## TL;DR

The download is not the slow part, and Whisper is not the main slow part either. Most of the time goes to **re-encoding video on the CPU in ways the product doesn't need**:

1. **Every 5-minute chunk is re-encoded into a 720p "proxy" with the slow default x264 preset.** On this machine that takes **460 s for 300 s of video** (1080p60 source), before transcription even starts. That is about 1.5× slower than real time, for a file that is only used for preview and face sampling.
2. **Every short is encoded with the default `medium` preset and seeks the slow way.** `-ss` sits after `-i`, so ffmpeg decodes *and runs the full crop + scale + caption + loudnorm chain* on every frame from 0 up to the clip start, then throws it away. Single exports in the DB took **4–9 minutes each**.
3. **Everything runs one job at a time** (`max_jobs=1`), so ASR, proxy, and render for different chunks never overlap, even though Whisper only uses 4 of the 12 threads.
4. **The clip count applies to each chunk, not to the video.** A 15-minute video becomes 4 chunks, and each chunk plans its own clips (up to about 8 at 35 s each). So you render more shorts than you asked for, and render time grows with them.
5. **Smaller things:** the Whisper model is reloaded for every chunk, the source is decoded twice in `normalize`, Docker has 7.6 GB RAM shared with about 40 other containers, and some renders fail **after** encoding with a misleading font error that hid the real cause (finding 8). Whisper itself (108 s per 5 min on `base`) is a minor cost. Raising its thread count made it 5× **slower** in the benchmark.

Rough estimate for the 15-minute case. **Not chunking it** (finding 4), skipping or speeding up the proxy (1), and rendering 10 shorts with input seek + `veryfast` (2) gives about ASR 5–6 min + Gemini 1–2 min + renders about 7 min ≈ **13–15 minutes**, down from about 60. Queue splitting (3) would then hide ASR behind rendering. This is an estimate from the benchmark below, not a measured end-to-end run.

---

## Result after the fixes (18 Nov 2026, measured)

The same laptop, the rebuilt stack, a 15:24 YouTube talk (`krsBRQbOPQ4`), Link form, autopilot, 10 clips:

| Stage | Seconds | Before (same-length video, 4 chunks) |
| --- | --- | --- |
| ingest (download, 477 MB at about 0.8 MB/s) | 584 | overlapped with processing by the fetcher |
| normalize (WAV now, proxy in background) | **1.7** | about 4 × 460 |
| asr (`base`, 924 s of audio) | 333.5 | about 4 × 110 |
| segment_score (Gemini timed out at 90 s, then 503, then the heuristic fallback) | 102.1 | about 4 × 50 |
| post_metadata (Gemini daily quota hit, flash-lite answered) | 5.1 | — |
| proxy_wait | **0.0** (already done during ASR) | — |
| render_clips: **10 of 10** ready, 36–41 s each | **231** (about 23 s per short) | about 135 s per short, often more |
| **Total** | **1,258 s (21 min)**, of which processing **674 s (11.2 min)** | about 60–90 min |

What this shows:

- Processing went from about an hour to **about 11 minutes**. The download is now the largest single cost: 9.7 minutes, at about 0.8 MB/s from YouTube.
- The Gemini rank spent 102 s and then fell back to the heuristic ranking. The project is also on the free-tier daily quota (`429 GenerateRequestsPerDayPerProjectPerModel-FreeTier`). That is a network and quota problem, not CPU.
- Next lever if needed: start ASR on the audio while the video is still downloading (download audio-only first, then video), or lower `GEMINI_TIMEOUT_SEC` so a hung call costs 30 s instead of 90 s.

---

## The machine

| | |
| --- | --- |
| CPU | Intel Core i7-9750H, 6 cores / 12 threads, 2.6 GHz (2019 MacBook Pro class) |
| GPU usable by the pipeline | None. Docker on macOS can't reach VideoToolbox or Metal, and there's no NVIDIA GPU. |
| Docker VM | x86_64, 12 CPUs, **7.66 GiB RAM**, shared with about 40 other running containers (Elasticsearch, Supabase ×2, Gravitee, Mongo, several Postgres) using roughly 5 GB of it |
| Whisper model in `.env` | `faster-whisper-base`, CPU, int8 |
| Source format from `yt-dlp` chunks | H.264 **1920×1080 @ 60 fps** + AAC |

60 fps 1080p is the expensive case for every decode and encode step. Each second of source is 60 frames to decode, scale, and encode.

---

## What the database says (real runs)

From the `jobs` table, URL project `d4b4cb9e…` (a long VOD split into 300 s chunks) and the 16 Nov live project:

| Window (300 s of video) | Pipeline wall time | Shorts rendered |
| --- | --- | --- |
| live 1 | 983 s (16.4 min) | 5 |
| live 2 | 947 s (15.8 min) | 5 |
| VOD 1 | 715 s (11.9 min) | 4 |
| VOD 2 | 687 s (11.5 min) | 1 |
| VOD 3 | 1265 s (21.1 min) | 2 |
| VOD 4 | 1873 s (31.2 min) | 3 (1 ok, 2 failed) |
| VOD 5 | 867 s (14.5 min) | 3 (all failed) |
| VOD 6 | 1031 s (17.2 min) | 3 (all failed) |
| VOD 7 | 1598 s (26.6 min) | 1 |
| VOD 8 | 1984 s (33.1 min) | 5 |
| VOD 9 | 1428 s (23.8 min) | 3 (all failed) |

So **each 5 minutes of video costs 11–33 minutes of processing, about 2.3–6.6× slower than real time.** A 15-minute video is 4 chunks (300 s with a 280 s step), which is about **60–90 minutes**. That matches what you're seeing.

Single manual exports in the same project took **528 s, 341 s, 282 s, 242 s** for one short each. Four more ran 51–368 s and then **failed**. The stored reason was `Error opening memory font 'DejaVu-LICENSE'`, which turned out to be a harmless warning (see finding 8).

The fetcher was never the bottleneck. Chunk downloads were queued about every 3 minutes (chunk `created_at` 11:34, 11:37, 11:39, 11:43…), while the worker took 12–33 minutes per chunk. The downloader ran dozens of chunks ahead of the worker.

---

## Benchmark (worker container, chunk `0005.mp4`, 300 s, 1080p60)

Each command ran alone in the worker container with nothing else queued. "Export" is one 35 s short starting at 240 s into the chunk, cropped to 9:16, scaled to 1080×1920, with loudnorm.

| Step | Command shape | Time | vs. real time (300 s) |
| --- | --- | --- | --- |
| Proxy 720p, x264 default `medium` (today) | `normalize.py` as written | **460 s** | 1.53× slower |
| Proxy 720p, x264 `veryfast` | same + `-preset veryfast` | **222 s** | 0.74× |
| WAV extract 16 kHz mono | `normalize.py` as written | **2 s** | — |
| Export 35 s short, **output** seek, `medium` (today) | `export.py` as written (without ASS) | **135 s** | — |
| Export 35 s short, **input** seek, `medium` | `-ss` before `-i` | **81 s** | −40 % |
| Export 35 s short, input seek, `veryfast` | + `-preset veryfast` | **38 s** | **3.5× faster than today** |
| Whisper `base` int8, defaults (today: beam 5, no VAD, 4 threads) | `asr.py` as written | **108 s** (+0.8 s model load) | 0.36× |
| Whisper `base` int8, beam 1 + VAD + `cpu_threads=10` | tweak | **564 s** | **5× slower**, see finding 5 |

Not measured: `-r 30`, skipping the proxy entirely, the ASS caption filter (left out of the export test), and beam 1 / VAD with the default 4 threads.

What this means for one 300 s chunk today: proxy ≈ 460 s + ASR ≈ 110 s + Gemini ≈ 70 s + 3 shorts × ≈ 135 s ≈ **1,050 s (17–18 min)**. That matches the DB median. The proxy and the renders account for about 80 % of it.

---

## Where the time goes, in pipeline order

The pipeline per chunk is `ingest → normalize → asr → segment_score → propose_windows → post_metadata → render_clips` in [`runner.py`](../services/api/app/pipeline/runner.py), all in one Arq job.

### 1. `normalize` re-encodes the whole chunk to a 720p proxy with x264 `medium`. This is the biggest single cost.

[`normalize.py:22-43`](../services/api/app/pipeline/normalize.py)

```text
ffmpeg -i raw -vf scale=-2:min(720,ih) -c:v libx264 -pix_fmt yuv420p -c:a aac ... proxy.mp4
```

- No `-preset`, so x264 uses `medium`. No `-r`, so a 60 fps source stays 60 fps.
- Measured: **460 s for a 300 s chunk.** The 16 Nov R&D note measured 364 s on live window 1.
- What the proxy is used for: browser preview, face sampling ([`faces.py`](../services/api/app/pipeline/faces.py) `analysis_video`), and an export fallback. None of these need a high-quality 60 fps encode.
- The chunk from `yt-dlp` is already H.264/AAC MP4 at 1080p or less (`SECTION_FORMAT` in [`vod/argv.py`](../services/api/app/vod/argv.py)). Browsers can play it directly.
- Then the same file is decoded **a second time** for the WAV (`normalize.py:46-59`).

**Options, cheapest first:**

| Change | Expected effect |
| --- | --- |
| Add `-preset veryfast` (or `ultrafast`) and `-r 30` to the proxy encode | Measured: `veryfast` alone is 460 s → 222 s. `-r 30` / `ultrafast` not measured; 1080p60 decode + scale stays a fixed cost either way. |
| Produce WAV and proxy in **one** ffmpeg call with two outputs (`-map 0:a ... audio.wav -map 0:v -map 0:a ... proxy.mp4`) | Saves a full decode of the source |
| Run ASR on the WAV **before or in parallel with** the proxy (WAV extraction took a few seconds) | Transcript and ranking no longer wait on the proxy. Only face crop and preview need it. |
| For chunks (already H.264 ≤1080p) **skip the proxy** and point preview and face sampling at the chunk itself. Face sampling already uses input seeking and decodes only 24 frames. | Removes this stage entirely for VOD and live |
| Generate the proxy lazily, only when the review UI asks for it | Same as above for manual projects |

### 2. Rendering each short: output seeking + `medium` preset + full-res 60 fps

[`export.py:_encode_short`](../services/api/app/pipeline/export.py) (around line 241)

```text
ffmpeg -fflags +discardcorrupt -err_detect ignore_err -i SOURCE -ss T -t D -vf crop,scale=1080:1920,ass=... -af loudnorm ... -c:v libx264 ...
```

Three separate costs stack here:

**a. `-ss` is after `-i` (output seeking).** ffmpeg decodes every frame from 0 to `T` and then throws it away. A clip near the end of a 5-minute 60 fps chunk wastes about 14,000–18,000 frames of 1080p decode. (Checked with `showinfo` on ffmpeg 7.1: the discarded frames are dropped before the filter graph, so crop, scale, and ASS are not run on them. The waste is decode only. Measured: 135 s → 81 s.) For a non-chunked upload (a file under 5 minutes, or a manual export from a full raw) the waste grows with the clip start time. The code comment says this was chosen so "a cut chunk is decoded from its start", meaning stream-copied chunks that begin on a non-keyframe. The standard fix keeps that safety:

```text
ffmpeg -ss max(0,T-5) -i SOURCE -ss (T - max(0,T-5)) -t D ...
```

Fast keyframe seek to 5 s before the clip, then accurate decode for the last 5 s. Since ffmpeg 2.1, plain input `-ss` with re-encoding is already frame-accurate, so even `-ss T -i SOURCE` alone is fine. Add `-avoid_negative_ts make_zero` on the chunk copy if edge cases show up.

**b. No `-preset`, so x264 uses `medium` at 1080×1920.** For 15–60 s social clips, `veryfast` or `faster` with `-crf 20–22` looks close to the same after YouTube/TikTok re-encode, at a fraction of the time.

**c. 60 fps output.** Shorts don't need 60 fps for talking heads. `-r 30` (or `fps=30` in the filter graph) halves the encode work. Keep 60 only for a "gameplay" framing if you want it.

Also in this stage:

- **`loudnorm` single pass** is fine for speed. Two-pass would be slower, so don't add it.
- **Face detection** ([`faces.py`](../services/api/app/pipeline/faces.py)) already uses input seeking and at most 24 JPEG frames. It's cheap, so leave it.
- **Probe repeated per clip:** `render_export` runs `ffprobe` on the source for every clip, plus `_window_row` opens a new DB session and scans all `LiveWindow` rows **3 times per clip** (`_window_media`, `_words_file`, `_analysis_video`). That's small next to encoding, but it's easy to cache once per job.

### 3. Everything is serial: `max_jobs = 1`

[`worker.py:85`](../services/api/app/worker.py): one Arq job at a time, and each job runs all 7 stages back to back for one chunk.

- Whisper (CTranslate2) on CPU uses **4 threads by default** (`cpu_threads=0` → 4). On a 12-thread VM that leaves about 2/3 of the CPU idle during ASR.
- x264 uses all threads during proxy and render, so two encodes at once don't help. Overlapping **ASR of chunk N+1 with rendering of chunk N** does help.
- Exports from the UI go to the same queue, so a manual "Export" click waits behind whole chunk pipelines.

**Options:**

| Change | Effect |
| --- | --- |
| Split into two queues / worker processes: **analyze** (ingest, WAV, ASR, score, windows, metadata) and **render** (proxy if kept, render_clips, exports) | Transcription of the next chunk runs while the current chunk renders. The CPU stays busy and the first shorts appear sooner. |
| Put user-clicked exports on their own queue (or give them priority) | UI exports don't wait 15+ minutes behind autopilot |
| Turn on SQLite WAL + `busy_timeout` before adding a second writer process (the VOD R&D doc already calls this out) | Avoids `database is locked` |
| Back-pressure the fetcher (only fetch chunk N+1 when chunk N's analysis has started) | Avoids the 60+ chunks queued ahead seen in the DB, and the disk that uses |

### 4. Clip count is per chunk, not per video, so render volume multiplies

[`segment_score.py:42-44`](../services/api/app/pipeline/segment_score.py) and [`clip_plan.py`](../services/api/app/pipeline/clip_plan.py): each chunk job plans `min(requested, speech_seconds / 35)` clips, plus 5 spares for the ranker. A 300 s chunk with continuous speech supports about 8. A 15-minute video in 4 chunks can therefore render **up to about 30 shorts** when you asked for 10. The DB shows 1–5 rendered per chunk, all of which cost a full encode.

**Options:**

- Split the project's requested count across chunks (for example `ceil(requested × chunk_speech / total_speech)`), or
- For sources under about 20–30 minutes, **don't chunk at all.** Download the whole file (it's small), run one transcript and one ranking over the whole video, and render only the top N. Chunking was built for 11-hour VODs ([VOD_CHUNK_POOL_RND.md](./VOD_CHUNK_POOL_RND.md)). At 15 minutes it just adds 4× the fixed costs (proxy, model load, two LLM calls, overlap work) and weakens ranking, because the LLM never sees the whole video.
- Raise the chunk threshold separately from `LIVE_CHUNK_SEC`. Today `_create_from_url` / upload chunk anything over `live_chunk_sec` (300 s, [`projects.py:283, 336`](../services/api/app/api/routes/projects.py)). A separate `VOD_CHUNK_THRESHOLD_SEC=1800` would keep short videos on the single-file path.

### 5. Whisper settings

[`asr.py:48-56`](../services/api/app/pipeline/asr.py)

- `model.transcribe(wav, word_timestamps=True)` uses defaults: **`beam_size=5`, `vad_filter=False`**, 4 CPU threads.
- Measured with defaults: **108 s for 300 s of audio** on `base`. That's about 10 % of a chunk's time, so ASR isn't the main problem.
- **Don't raise `cpu_threads`.** The run with `beam_size=1` + `vad_filter=True` + `cpu_threads=10` took **564 s, 5× slower**. The likely cause is thread oversubscription on 6 physical cores (hyperthreads plus CTranslate2/OpenMP pools) and memory pressure in the 7.6 GB VM. Because three settings changed at once, it's not known which one did it.
- Still worth testing **one at a time**, keeping 4 threads: `beam_size=1` (greedy is usually faster) and `vad_filter=True` (skips silence and music, useful for streams). Keep whichever actually measures faster.
- **The model is constructed on every job** (`WhisperModel(...)` inside `run_asr`). With `base` that's a few seconds. With `medium` (the `.env.example` default) it's much more per chunk. Cache it in a module-level variable like `faces._detector` already does.
- Keep `base` or `small` on this CPU. Don't use `medium` or `large` without a GPU.
- Measured ASR numbers are in the benchmark table above.

### 6. LLM calls (Gemini)

Two calls per chunk (rank + post metadata), each with a 90 s timeout and one retry ([`llm/gemini.py`](../services/api/app/llm/gemini.py)). The 16 Nov measurement was 46 s for rank, and the DB shows 26 s for post metadata. That's about 1–2 minutes per chunk, so 4–8 minutes per 15-minute video, spent while the CPU sits idle. Not chunking short videos (finding 4) cuts this to 2 calls total. Running metadata generation off the critical path (after renders start) hides it.

### 7. Docker memory and neighbours

The Docker VM has 7.66 GiB and roughly 5 GB is already used by unrelated stacks (Elasticsearch 1.1 GB, two Supabase stacks, Gravitee, Mongo…). Whisper + ffmpeg 1080p filter graphs + mediapipe in the worker can push the VM into swap, which slows every stage unpredictably. That may explain the spread from 11 to 33 minutes for the same-size chunk. Stop unused stacks while clipping, or raise Docker Desktop's memory limit.

### 8. Renders that fail after spending the encode time, with a misleading error

Four exports and several autopilot renders failed with:

```text
[Parsed_ass_2] Error opening memory font 'DejaVu-LICENSE'
```

In the DB, VOD windows 5, 6, and 9 rendered **0 of 3**, and some of those failures came after 6 minutes of work.

**Correction (18 Nov, follow-up test):** this font message is **not** the cause. The same ASS + `fontsdir` render with the license file present exits 0. libass only warns. `_last_error_line` in `ffmpeg_util.py` picks the last stderr line containing "error", so a harmless warning was stored as the failure reason and the real cause was hidden. The pattern (long runtime, then failure) fits a process killed by the OOM killer or a container stop, which leaves only warnings in stderr. That is consistent with finding 7, but not proven, because those logs are gone.

Fixed in the implementation pass: the license moved to `assets/licenses/`, `run_cmd` now reports a signal exit as "ffmpeg was killed (signal N)…", and font warnings are never chosen as the failure line. The next failure will show its real cause.

### 9. Live streaming specifics

[`live/capture.py`](../services/api/app/live/capture.py) and [`live/cut.py`](../services/api/app/live/cut.py)

- Window cutting uses `-c copy`, which is cheap. Capture isn't the problem.
- `_drain` runs `ffprobe` on the growing `follow.mp4.part` every 2 s. That's cheap but unnecessary. Every 10–15 s is enough when windows are 300 s.
- The real problem is throughput: a window arrives every **280 s**, but processing a window takes **700–2000 s**. The backlog grows without limit, so clips fall further behind the live edge the longer the stream runs. For live to keep up, per-window processing must be **under 280 s**. That needs findings 1, 2, 3, and 5 together (skip or speed up the proxy, fast render preset at 30 fps, overlap ASR with render, faster Whisper). Otherwise, render fewer shorts per window.
- Consider **not rendering on every window** during live. Propose clips per window (cheap: ASR + rank), and render only the top-scoring ones, or render after Stop.

### 10. Small items (low impact, listed for completeness)

- `ingest` computes SHA-256 of every chunk (about 0.5–1 s per 100 MB). This is fine.
- `update_job` commits to SQLite on every progress tick. Fine at current volumes.
- `_window_row()` scans every `LiveWindow` row for the project 3× per clip. Use a direct query by `stream_start_sec`, or pass the row through.

---

## Recommended order of work

| # | Change | Effort | Expected gain on the 15-min case |
| --- | --- | --- | --- |
| 1 | Report the real ffmpeg failure (signal / OOM) instead of the font warning; move `DejaVu-LICENSE` out of `assets/fonts` | Trivial | The next failed render says why |
| 2 | Proxy: skip it for chunks, or at least `-preset veryfast`, and one ffmpeg call for WAV + proxy | Small | −4 min (veryfast) to −7.7 min (skip) **per chunk** |
| 3 | Export: input seek (`-ss` before `-i`, with a short accurate pre-roll), `-preset veryfast -crf 21`, optionally `-r 30` | Small | Measured 135 s → 38 s per 35 s short |
| 4 | Don't chunk sources under about 30 minutes (separate threshold), or divide clip count across chunks | Small–medium | Fewer shorts to render, 2 LLM calls instead of 8, better ranking |
| 5 | Whisper: cache the model; test `beam_size=1` and `vad_filter=True` **one at a time** at 4 threads | Small | Small (ASR is about 10 % of the time); don't raise `cpu_threads` |
| 6 | Split analyze / render queues; exports on a priority queue; SQLite WAL | Medium | ASR and LLM hidden behind render, so first shorts arrive sooner |
| 7 | Free Docker RAM (stop other stacks) or raise the limit | None | Removes the 11 → 33 min variance |
| 8 | Live: render only the top N per window, or render after Stop | Medium | Lets live keep up with the stream |

**Bigger levers if that's still not enough:**

- **Run the worker natively on macOS** (not in Docker) and use `h264_videotoolbox` for proxy and render. The Intel Mac's hardware encoder is several times faster than x264 `medium` and uses almost no CPU. Docker can't reach it.
- **Hosted ASR** (Gemini audio input is already wired for text, or any Whisper API). A 5-minute WAV transcribes in seconds instead of competing with x264 for the CPU.
- **A Linux box with an NVIDIA GPU**: `h264_nvenc` + CUDA Whisper. That turns this into a faster-than-real-time pipeline. [CLIPPING_ARCHITECTURE.md](./CLIPPING_ARCHITECTURE.md) already lists it as the "comfortable" tier.

---

## How to measure after each change

`call_stage` already logs `stage finished` with `duration_ms` per stage ([`stage_log.py`](../services/api/app/pipeline/stage_log.py)), but container logs are lost on restart and the DB keeps only whole-job times. Before changing anything, consider writing `duration_ms` per stage into the job's `checkpoint` (for example `checkpoint["timings"][stage]`). That way every run leaves a per-stage breakdown in `cliping.db`, and you can compare before/after with one SQL query instead of guessing.

Until then:

```bash
docker compose logs --no-color worker | grep '"stage finished"'
```
