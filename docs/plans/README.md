# Cliping — Implementation Plans (Agent Playbooks)

**Workspace:** `/Users/apple/Desktop/Cliping`  
**Source of truth (architecture):** [../CLIPPING_ARCHITECTURE.md](../CLIPPING_ARCHITECTURE.md)  
**Background research:** [../CLIPPING_RND.md](../CLIPPING_RND.md)  
**Document date:** November 2026  

These docs are **executable playbooks**. An agent (or human) should complete **one phase per run**, in order, without skipping ahead.

**Project skills** (workflows): [`.cursor/skills/README.md`](../../.cursor/skills/README.md) — phase-runner, frontend, backend, media-pipeline, docker, web-research.  
**Project rules** (always-on standards): [`.cursor/rules/`](../../.cursor/rules/).

---

## Phase index

| Order | Doc | Goal | Depends on |
| --- | --- | --- | --- |
| **0** | [PHASE_0_SKELETON.md](./PHASE_0_SKELETON.md) | Runnable monorepo: API health, SQLite, Redis/Arq, Next shell, Compose | — |
| **1** | [PHASE_1_MANUAL_CLIPPER.md](./PHASE_1_MANUAL_CLIPPER.md) | Upload → ASR → manual trim → 9:16 captioned MP4 | Phase 0 |
| **2** | [PHASE_2_RANK_AND_URL.md](./PHASE_2_RANK_AND_URL.md) | Auto proposals (heuristics + Ollama) + yt-dlp URL + review gallery | Phase 1 |
| **3** | [PHASE_3_REFRAME_POLISH.md](./PHASE_3_REFRAME_POLISH.md) | Face crop, karaoke ASS, caption overrides, idempotent re-export | Phase 2 |
| **4** | [PHASE_4_HARDENING.md](./PHASE_4_HARDENING.md) | Auth, cleanup, storage interface, ops README, VPS-ready | Phase 3 |
| **5** | [PHASE_5_AUTOPILOT_CLIPS.md](./PHASE_5_AUTOPILOT_CLIPS.md) | Paste link → 10+ rendered Shorts with Gemini title, description, hashtags | Phase 4 |
| **6** | [PHASE_6_YOUTUBE_PUBLISH.md](./PHASE_6_YOUTUBE_PUBLISH.md) | Connect YouTube once; one click posts the batch, now or spaced | Phase 5 |
| **7** | [PHASE_7_TIKTOK_PUBLISH.md](./PHASE_7_TIKTOK_PUBLISH.md) | Connect TikTok once; one click posts the batch. Separate from YouTube. | Phase 5. Not Phase 6. |
| **8** | [PHASE_8_LIVESTREAM_CLIPS.md](./PHASE_8_LIVESTREAM_CLIPS.md) | Clips from a YouTube livestream that is still on air, or from a folder of finished local segments. | Phase 2. Independent of Phase 6 and Phase 7. |
| **9** | [PHASE_9_VOD_CHUNKS.md](./PHASE_9_VOD_CHUNKS.md) | A finished video longer than 5 minutes is fetched in 5-minute ranges. One process downloads the next range while the pipeline runs the current one. | Phase 8. |

Research behind Phases 5–8: [../AUTOPILOT_RND.md](../AUTOPILOT_RND.md), [../YOUTUBE_PUBLISH_RND.md](../YOUTUBE_PUBLISH_RND.md), [../TIKTOK_PUBLISH_RND.md](../TIKTOK_PUBLISH_RND.md), [../LIVESTREAM_CLIPS_RND.md](../LIVESTREAM_CLIPS_RND.md). Chunked download of a finished long video is [../VOD_CHUNK_POOL_RND.md](../VOD_CHUNK_POOL_RND.md) and is Phase 9. Instagram publish is researched and is not a phase: [../INSTAGRAM_PUBLISH_RND.md](../INSTAGRAM_PUBLISH_RND.md). A browse tab of public live videos is in [../YOUTUBE_LIVE_DISCOVERY_RND.md](../YOUTUBE_LIVE_DISCOVERY_RND.md). The Live card’s Live now tab is implemented and stays off until `YOUTUBE_API_KEY` is set. Search returns a ranked sample, not every stream. Using live chat to skip quiet minutes of a stream is researched and is not a phase: [../LIVE_CHAT_SPIKE_RND.md](../LIVE_CHAT_SPIKE_RND.md). A short finished video stays on one-shot URL ingest. A longer finished video is Phase 9. Phase 7 code is in; the private TikTok smoke is not. Phase 8 does not wait on TikTok, and a TikTok run does not wait on Phase 8.

---

## Rules for agents (read before every phase)

1. **One phase per run.** Finish all tasks + exit criteria in the current phase doc before starting the next.
2. **Do not implement future-phase features** “while you’re there” (no yt-dlp in Phase 1, no Ollama in Phase 0/1, no Remotion until Phase 4 spike).
3. **Follow architecture defaults** in [CLIPPING_ARCHITECTURE.md §3 / §18](../CLIPPING_ARCHITECTURE.md): FastAPI + Arq + SQLite + FFmpeg + faster-whisper + Next.js + local-first.
4. **Fully local / free by default:** no required cloud API keys. Optional cloud only behind env flags.
5. **Rights:** Prefer upload of content the operator owns. URL ingest only in Phase 2+, with disclaimer. No DRM bypass, no cookie-harvest guides.
6. **Human-in-the-loop:** Never auto-publish. Posting needs an explicit Post click after the operator has seen the final title, description, channel, and visibility. Manual projects still need Accept before Export. Autopilot projects (Phase 5+) may render proposals without a click, because rendering only writes a local file.
7. **Idempotent stages:** Pipeline stages must skip if artifacts already exist (checkpoint / checksum).
8. **Verify exit criteria** before marking the phase done. Run the smoke checks listed in the phase doc.
9. **Update this checklist** when a phase completes: set status in the table below and note commit/PR if any.
10. **Conflict rule:** If a phase doc and architecture disagree on a tiny detail, prefer the **phase doc for that phase’s scope**, then sync architecture later. For stack/legal defaults, prefer architecture.

---

## Progress tracker

| Phase | Status | Completed date | Notes |
| --- | --- | --- | --- |
| 0 Skeleton | `done` | 2026-11-13 | Compose + health + Arq ping + Next shell |
| 1 Manual clipper | `done` | 2026-11-13 | Upload → Whisper → manual trim → 9:16 captions export |
| 2 Rank + URL | `done` | 2026-11-14 | Heuristic proposals, review gallery, gated URL ingest |
| 3 Reframe polish | `done` | 2026-11-14 | Face crop, karaoke ASS, caption override, loudnorm, idempotent export |
| 4 Hardening | `done` | 2026-11-14 | Auth, storage, cleanup, logs, watermark, ops. Soak not run: no owned source ≥30 min in workspace. Remotion deferred. |
| 5 Autopilot clips | `done` | 2026-11-15 | 10 ready 1080×1920 Shorts. Post text from gemini-3.5-flash (gemini-3.8-flash timed out). Ranking fell back: LLM_TOO_FEW_CLIPS. |
| 6 YouTube publish | `in_progress` | | Code, unit tests, and docs are in. Live connect and a private upload still need a Google OAuth client in `.env`. |
| 7 TikTok publish | `in_progress` | | Code and unit tests are in (17 Nov 2026). Private connect and a 2-clip Only me post still need a TikTok developer app in `.env`. YouTube modules were not edited. |
| 8 Livestream clips | `done` | 2026-11-16 | Live capture is off until `ALLOW_LIVE_INGEST=true`. Closed windows are cut from `follow.mp4.part` and run through the existing pipeline. Spike: yt-dlp 2026.10.26. |
| 9 VOD chunks | `in_progress` | | Code, tests, spike, and a 6-minute upload smoke are in (17 Nov 2026). One fetcher overlaps one pipeline. The 11-hour URL was not started. |

Status values: `not_started` | `in_progress` | `done` | `blocked`

---

## How to prompt an agent for a phase

Use a prompt like:

```text
Implement Phase N of Cliping exactly as specified in:
docs/plans/PHASE_N_....md

Also follow docs/CLIPPING_ARCHITECTURE.md for stack, models, env vars, and API shapes.
Complete every task in the phase doc. Do not start Phase N+1.
Verify all exit criteria and update docs/plans/README.md progress tracker.
```

For Multitask / multi-agent: only split work **inside** a phase if the phase doc has an explicit **Parallelization** section. Never run Phase 1 and Phase 2 agents at the same time on an empty/shared tree without a shared owner for DB models.

---

## Locked stack (do not thrash)

| Layer | Choice |
| --- | --- |
| API | Python 3.12 + FastAPI |
| Worker | Redis + Arq (`max_jobs=1`) |
| DB | SQLite + SQLAlchemy 2 + Alembic |
| UI | Next.js 15 (App Router) + TypeScript |
| Media | FFmpeg |
| ASR | faster-whisper |
| LLM (Phase 2+) | Ollama local (optional `openai_compatible`) |
| Deploy | Docker Compose |

---

## Suggested first agent run

1. Complete **Phase 0** end-to-end.  
2. Immediately continue with **Phase 1** (or a fresh agent with only Phase 1 doc).  
3. Stop and demo: upload owned MP4 → download vertical subtitled clip.  
4. Only then start Phase 2 (URL + Ollama).
