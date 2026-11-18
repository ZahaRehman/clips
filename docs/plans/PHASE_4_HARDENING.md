# Phase 4 — Hardening & Growth Hooks

**Depends on:** Phase 3 complete  
**Estimated effort:** ~1 week  
**Architecture refs:** §10.2 VPS, §11 secrets, §15 risks, §17 security  

---

## 0. Goal

Make Cliping safe and operable as a **personal one-box deploy** (laptop or single VPS):

1. Storage interface (local now, S3/R2-ready later).  
2. Optional Postgres flag.  
3. Basic auth / API token enforcement for non-localhost.  
4. Cleanup jobs for temps/failed artifacts.  
5. Ops metrics/logging + solid README.  
6. Optional brand watermark.  
7. Remotion spike **only if** FFmpeg captions/style still insufficient.

---

## 1. Out of scope

- Multi-tenant SaaS, billing, orgs  
- Mobile apps  
- Autopost calendars  
- Guaranteed “viral score” marketing  

---

## 2. Tasks

### Task 4.1 — Storage interface

**Do:**
- Abstract `Storage` protocol: `put`, `get`, `open`, `delete`, `url_for` (local path or signed URL later).  
- Default `LocalStorage(DATA_DIR)`.  
- Stub `S3Storage` behind env `STORAGE_BACKEND=local|s3` without requiring S3 to run tests.  
- All pipeline paths go through storage helpers (no scattered absolute path assumptions beyond config root).

**Done when:** Local backend unchanged for users; S3 interface exists and is documented as unused-by-default.

---

### Task 4.2 — Postgres optional

**Do:**
- Support `DATABASE_URL` for Postgres (`asyncpg` or sync equivalent—match existing async style).  
- Keep SQLite as default in `.env.example`.  
- Document migration notes (Alembic should work for both).

**Done when:** Compose profile or doc shows Postgres optional; SQLite still default smoke path.

---

### Task 4.3 — Auth for personal VPS

**Do:**
- Enforce `API_TOKEN` (Bearer or `X-API-Token`) on all non-health routes when `API_TOKEN` is set.  
- Next.js: send token from env `CLIPING_API_TOKEN` / server-side only when possible.  
- Document Tailscale / bind localhost / Caddy basic auth alternatives.  
- **Never** expose URL-ingest publicly without token.

**Done when:** With token set, unauthenticated API calls get 401; health may remain public.

---

### Task 4.4 — Cleanup & disk guards

**Do:**
- Pre-ingest free-disk check; fail early with clear error.  
- Periodic or on-demand cleanup: delete `proxy` optional, temp FFmpeg files, failed project partials older than N days (`CLEANUP_MAX_AGE_DAYS`).  
- `POST /api/v1/admin/cleanup` (token-protected) or Arq cron.  
- Log bytes reclaimed.

**Done when:** Filling disk is less likely; cleanup documented.

---

### Task 4.5 — Logging & metrics

**Do:**
- Structured logs: project_id, job_id, stage, duration_ms.  
- Simple counters in logs or `/api/v1/health` extended: jobs succeeded/failed (in-memory OK for personal).  
- No PII beyond what operator already stores; don’t log full URLs with secrets.

**Done when:** Debugging a failed export from logs is realistic.

---

### Task 4.6 — Optional watermark

**Do:**
- Env `WATERMARK_TEXT` / `WATERMARK_IMAGE` optional.  
- FFmpeg drawtext/overlay at export if set.  
- Default off.

**Done when:** Enabling env shows watermark; default exports unchanged.

---

### Task 4.7 — Remotion spike (optional gate)

**Do only if** operator still unhappy with ASS styling after Phase 3:
- Spike folder `apps/remotion-spike` or docs note.  
- Prove one template; do **not** replace FFmpeg path as default.  
- If skipped, write `docs/plans/REMOTION_DEFERRED.md` with why.

**Done when:** Either spike exists with README, or explicit deferral note committed.

---

### Task 4.8 — Ops README / runbook

**Do:** `docs/OPS.md` covering:
- Backup: `data/` + DB file  
- Model cache: `models/`  
- GPU compose notes  
- Updating yt-dlp / Whisper models  
- Disaster: wipe project dir  
- Security: token, Tailscale, don’t public-bind URL ingest  

Root README links to OPS.md.  
Update plans progress tracker Phase 4 = `done`.

**Done when:** Fresh VPS deploy instructions are followable without chat history.

---

### Task 4.9 — End-to-end soak

**Do:**
- Run one **≥30–120 min** owned source through: ingest → proposals → accept → export ≥3 clips.  
- Confirm restart mid-job + reprocess works.  
- Record wall times in OPS or completion report.

**Done when:** Soak passes; issues filed as follow-ups (not silent).

---

## 3. Parallelization

| Agent A | Agent B |
| --- | --- |
| 4.1–4.2 storage/DB | 4.3–4.5 auth/cleanup/logs |
| 4.6 watermark | 4.8 docs |
| One agent: 4.7 gate + 4.9 soak | |

---

## 4. Exit criteria

- [x] Local storage default; S3 interface stubbed.  
- [x] SQLite default; Postgres documented/optional.  
- [x] API token enforced when configured.  
- [x] Cleanup + disk guard exist.  
- [x] OPS.md written; README links it.  
- [ ] Soak test on long owned video recorded. Not run: workspace source is a short upload. Script: `scripts/soak_phase4.sh`.  
- [x] Remotion spiked or explicitly deferred. See [REMOTION_DEFERRED.md](./REMOTION_DEFERRED.md).  
- [x] Progress tracker: Phase 4 = `done` (soak called out in the tracker note).  
- [x] Architecture still accurate; deltas are in [OPS.md](../OPS.md).

---

## 5. Agent completion report

```text
Phase 4 complete.
- Deploy mode verified: local compose / VPS
- Auth: token yes/no
- Soak: duration, clip count, wall time
- Remotion: spiked / deferred
- Product ready for personal daily use: yes/no
```

---

## 6. After Phase 4

Backlog ideas (not this phase):
- Active speaker / split screen  
- “Find the part about X” search  
- Social scheduling + analytics feedback loop  
- Multi-user accounts  

Return to [CLIPPING_RND.md §7](../CLIPPING_RND.md) for build-vs-buy growth ideas.
