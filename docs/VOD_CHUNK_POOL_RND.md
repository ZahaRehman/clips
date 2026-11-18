# Finished long videos — chunked download and a worker pool

**Workspace:** `/Users/apple/Desktop/Cliping`  
**Research date:** 17 November 2026  
**Decision this unblocks:** Can a finished video of many hours be turned into shorts without downloading the whole file first, and can more than one worker do that at once?  
**Status:** Research. The playbook is [plans/PHASE_9_VOD_CHUNKS.md](./plans/PHASE_9_VOD_CHUNKS.md). No code in this pass.

This is not the live-follow path. A broadcast that is still on air stays on [LIVESTREAM_CLIPS_RND.md](./LIVESTREAM_CLIPS_RND.md). `--download-sections` is the wrong tool there. It is the right tool here, on a video that already has a finite duration.

---

## Verdict

Stop waiting for one 11-hour file. Probe the URL, plan 5-minute ranges, download one range, and run that file through the pipeline that live windows already use. While that pipeline runs, a second process downloads the next range. Clips from minute 0 can exist before minute 30 has been fetched.

Do not start a Python thread per chunk, and do not start every 5-minute range at once. On this machine the slow part is the encode and Whisper, not the transfer. One extra downloader hides the wait. A second full pipeline fights the first for CPU and makes both slower. A pool of a hundred `yt-dlp` processes will also get throttled and fill the disk with video the ranker has not looked at yet.

The measured example is the finished VOD `GpIWRLQBTvU` (17 Nov 2026, `yt-dlp -J`, no media written): `live_status=was_live`, `is_live=false`, duration **41601 s** (11 h 33 m). The best video stream is 1440p at about **41.7 GB**. The Link form’s 8 GB cap would still refuse a whole-file download after the 12-hour time cap. A 5-minute slice of that same stream is a few hundred megabytes, under the cap.

---

## What is slow today

Link ingest runs one `yt-dlp` to the end, then one Arq job (`max_jobs=1`) does ingest, normalize, ASR, rank, and render. Nothing else runs until that job returns.

A live window already showed the split. On window `0001` of the 16 Nov capture, normalize took **364 s**, ASR **47 s**, and the Gemini rank **46 s**, before any short was encoded. Normalize was one `ffmpeg` `libx264` pass over a 5-minute file. A later render of one short used several cores at once. Two of those encodes at the same time would split the same cores.

So for a long finished video the order of cost is:

1. Re-encoding each chunk (and later each short).
2. Whisper on that chunk.
3. The download, once the range is a few minutes instead of the whole file.

Overlapping the next download with step 1 is worth it. Overlapping a second copy of step 1 is not, until a spike shows idle CPU.

---

## Options compared

| Option | Fit for Cliping | Cost | Local? | Risk |
| --- | --- | --- | --- | --- |
| **One section download at a time, one pipeline job at a time, overlapped** | Reuses live windows, overlap rejection, and the project page. First clips appear after one chunk, not after 11 hours. | $0. Disk for a couple of chunks plus their proxies, not the 42 GB master. | Yes | Section fetch can 403 if the format is a single progressive file. Keyframe cuts can start a few seconds early. |
| **Python threads inside one process, one thread per chunk** | Looks like the ask. `ffmpeg` and `yt-dlp` are subprocesses, so the GIL does not block them. | $0 | Yes | Shared SQLite sessions, one Whisper model, no back-pressure. Threads do not give a second CPU for `libx264`. Hard to cancel and retry. |
| **Many Arq workers, each downloading a chunk and then running the pipeline** | The right shape later, as processes, not threads. | $0 | Yes | Default of “all chunks at once” is about 140 jobs for this VOD. YouTube throttles parallel extractors. SQLite has one writer. CPU is already full at one encode. |
| **Download the whole file, then FFmpeg-split it** | Simple. | $0 | Yes | Defeats the request. This VOD does not fit the 8 GB cap, and clips wait until the last byte. |
| **`--force-keyframes-at-cuts` on every section** | Frame-exact 5-minute edges. | $0 | Yes | yt-dlp documents this as a re-encode and slow. It duplicates the proxy encode the pipeline already does. |
| **Keep today’s one-shot URL download for short videos** | Already built. | $0 | Yes | Right for a file under one chunk. Wrong above that. |

---

## Recommendation

Add a chunked path for a finished URL or upload whose duration is longer than one chunk. Leave live capture alone. Leave short URL downloads alone.

**Ship**

1. A probe that reads duration and `live_status` and does not write media. `is_live` still goes to the live form. `was_live` and `not_live` with a finite duration use this path when the duration is over 5 minutes.
2. Ranges of 300 s, step 280 s, same overlap the live cutter uses. The last range can be shorter. Do not re-encode the cut. A keyframe may include a few extra seconds; the 20 s overlap and the existing duplicate-proposal rejection cover that.
3. Each range is a file under the project, then the existing pipeline job. Clip times stay inside that file. `stream_offset_sec` is the range start, so the gallery can show the time in the original video.
4. Two processes from the same image. One only downloads the next range. One only runs the pipeline. Prefetch depth is 1: the downloader may be one range ahead, not twenty.
5. Section download uses `yt-dlp --download-sections` with a DASH video+audio format capped at 1080p (`avc1` + `m4a`). No cookies, no login, no bypass flags. If FFmpeg returns 403, fail that range with the upload message. Do not add extractor args to get past it.
6. After a chunk’s shorts are rendered, delete that chunk’s source file. Keep the exports. An 11-hour job must not leave 11 hours of masters on disk.
7. The project page shows clips as each chunk finishes. Same framing choice as today (`face` or `gameplay`).

**Do not ship in the first slice**

- A thread pool.
- `VOD_PIPELINE_SLOTS` above 1, until a spike writes down idle CPU during a single normalize.
- `--force-keyframes-at-cuts`.
- Downloading every range before the first pipeline job.
- Changing the live follow, or using `--download-sections` on `is_live`.
- Raising the 8 GB cap. The cap applies to one chunk file, which a 5-minute 1080p section will pass.

**Later, only if the spike says the CPU is idle**

A second pipeline process (`VOD_PIPELINE_SLOTS=2`), still not one process per chunk. Turn on SQLite WAL and a busy timeout before that second writer. Keep the downloader at one range in flight.

**Python.** The pool is operating-system processes started by Compose (`arq` twice, different queues or function lists). Threads are only acceptable inside a single download if `yt-dlp -N` is fetching fragments of that one section. They are not the scheduler.

**Time.** One chunk costs on the order of the live window already measured: several minutes of encode, under a minute of ASR on the base model, then render. The first shorts show up after that, while later ranges are still queued. The full 11 h 33 m is about 149 ranges at a 280 s step. Wall time is roughly that count times the per-chunk pipeline time, with the download hidden behind it. It will not become “149 times faster.”

---

## Sources

Accessed 17 November 2026.

- yt-dlp README, `--download-sections` and `--force-keyframes-at-cuts`: [https://github.com/yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)
- yt-dlp FAQ, cuts land on the previous keyframe unless re-encoded: [https://github.com/yt-dlp/yt-dlp/wiki/FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ) (linked from issue [8011](https://github.com/yt-dlp/yt-dlp/issues/8011))
- yt-dlp maintainers, `--download-sections` does not apply to an active livestream: [issue 13175](https://github.com/yt-dlp/yt-dlp/issues/13175), already cited by the live R&D
- Local probe of `https://www.youtube.com/live/GpIWRLQBTvU`: `was_live`, 41601 s, best video about 41.7 GB. No file saved.
- Local worker log, 16 Nov 2026, project `64c36313-0035-4a42-9a3e-40ceddc47f9f`, window 0001: normalize 364450 ms, ASR 47453 ms
