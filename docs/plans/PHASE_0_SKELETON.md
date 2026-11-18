# Phase 0 — Skeleton

**Status target:** `done` before any media pipeline work  
**Estimated effort:** 1–3 days  
**Depends on:** nothing  
**Blocks:** Phase 1–4  
**Architecture refs:** §3 stack, §6 models (create stubs OK), §9 repo layout, §10 Compose, §11 env  

---

## 0. Goal

Ship a **runnable empty product shell**: monorepo, Docker Compose, FastAPI health, SQLite + migrations, Redis + Arq no-op worker, Next.js UI that can call the API, shared `data/` volume.

**No Whisper, no FFmpeg pipeline, no upload UI required beyond a placeholder.** Prove process wiring only.

---

## 1. Out of scope (do NOT build)

- Video upload / yt-dlp  
- faster-whisper / Ollama  
- Clip export / captions  
- Auth beyond optional `API_TOKEN` stub  
- Remotion, S3, Postgres  

---

## 2. Target repo layout (create exactly)

```text
Cliping/
  docs/
    CLIPPING_RND.md
    CLIPPING_ARCHITECTURE.md
    plans/                    # this folder
  apps/
    web/                      # Next.js 15 App Router + TypeScript
  services/
    api/                      # FastAPI + shared worker package
      pyproject.toml          # or requirements.txt + README
      app/
        __init__.py
        main.py               # FastAPI app
        config.py             # pydantic-settings from env
        db/
          session.py
          base.py
        models/               # SQLAlchemy models (minimal: Project stub OK)
        api/
          routes/
            health.py
        worker.py             # Arq WorkerSettings + ping job
  data/                       # gitignored runtime
  models/                     # gitignored weights (empty for now)
  docker-compose.yml
  .env.example
  .gitignore
  README.md
```

**Packaging rule:** API and worker share the same Python package (`services/api`). Two processes, one image.

---

## 3. Tasks (complete in order)

### Task 0.1 — Git hygiene & ignores

**Do:**
- Create `.gitignore` covering: `data/**`, `models/**`, `.env`, `__pycache__`, `.venv`, `node_modules`, `.next`, `*.pyc`, `.DS_Store`, SQLite files under data.
- Keep `data/.gitkeep` and `models/.gitkeep` so folders exist.

**Done when:** Accidental secrets/runtime media cannot be committed easily.

---

### Task 0.2 — Environment template

**Do:** Create `.env.example` with at least:

| Variable | Example |
| --- | --- |
| `DATA_DIR` | `/data` (Compose) or `./data` (local) |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/cliping.db` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `API_TOKEN` | (empty or `dev-token`) |
| `CORS_ORIGINS` | `http://localhost:3000` |
| `FFMPEG_PATH` | `ffmpeg` |

Copy to `.env` for local/Compose (do not commit `.env`).

**Done when:** Documented in README how to copy `.env.example` → `.env`.

---

### Task 0.3 — Python project bootstrap

**Do:**
- Python **3.12** project under `services/api`.
- Dependencies (minimum): `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`, `alembic`, `aiosqlite`, `pydantic-settings`, `arq`, `redis`, `httpx` (tests/health optional).
- `app/config.py` loads settings from env.
- `app/main.py` creates FastAPI app, mounts CORS from `CORS_ORIGINS`, includes routers.

**Done when:** `uvicorn app.main:app` starts locally (venv) without Redis if health doesn’t require it yet—or Compose path works (prefer Compose verification in 0.7).

---

### Task 0.4 — Health endpoint

**Do:**
- `GET /api/v1/health` → `{ "status": "ok", "ffmpeg": "<version or missing>", "redis": "ok|error" }` (best-effort checks; don’t crash if Redis down—report status).
- Also acceptable: `GET /health` alias that redirects or mirrors—but **canonical is `/api/v1/health`**.

**Done when:** Curl returns 200 JSON with `status: ok`.

---

### Task 0.5 — SQLite + Alembic

**Do:**
- SQLAlchemy engine/session using `DATABASE_URL`.
- At least one model table to prove migrations work. Recommended minimal `projects` table with: `id` (UUID string), `title`, `status`, `created_at`, `updated_at` — other columns can be added in Phase 1.
- Alembic initial migration; `alembic upgrade head` on startup **or** documented manual migrate step in README + Compose command.

**Done when:** After migrate, SQLite file exists under `DATA_DIR` and `projects` table is queryable.

---

### Task 0.6 — Redis + Arq worker

**Do:**
- `app/worker.py` with `WorkerSettings`, `redis_settings` from `REDIS_URL`, `max_jobs=1`.
- Register a `ping` job that writes a file `DATA_DIR/_worker_ping.txt` with a timestamp and returns `"pong"`.
- API route `POST /api/v1/dev/ping-worker` (dev-only is fine) that enqueues `ping` and returns job id **or** document how to enqueue via `arq` CLI.

**Done when:** Starting worker + enqueue results in ping file appearing in `data/`.

---

### Task 0.7 — Docker Compose

**Do:** `docker-compose.yml` services:

| Service | Role |
| --- | --- |
| `redis` | `redis:7-alpine`, port 6379 |
| `api` | build `services/api`, port 8000, volumes `./data:/data`, `./models:/models`, `env_file: .env` |
| `worker` | same image, command runs Arq worker, same volumes |
| `web` | build `apps/web`, port 3000, `NEXT_PUBLIC_API_URL=http://localhost:8000` |

**Notes:**
- Ensure `DATA_DIR=/data` inside containers.
- Worker `depends_on: redis` (and ideally api not required).
- Document Apple Silicon / ffmpeg later; Phase 0 image may be slim Python without Whisper.

**Done when:** `docker compose up --build` brings services up; health reachable from host.

---

### Task 0.8 — Next.js shell

**Do:**
- `apps/web` with Next.js 15 App Router + TypeScript.
- Home page shows product name **Cliping**, short subtitle, and API health status fetched from `${NEXT_PUBLIC_API_URL}/api/v1/health` (client or server component).
- Simple, clean layout—no marketing clutter. Dark or light fine; keep readable.
- `package.json` scripts: `dev`, `build`, `start`.

**Done when:** Opening `http://localhost:3000` shows health OK (or clear error if API down).

---

### Task 0.9 — Root README

**Do:** Root `README.md` with:
1. What Cliping is (one paragraph).  
2. Prerequisites: Docker (recommended) or Python 3.12 + Node 20 + Redis + FFmpeg.  
3. Quick start: copy env, `docker compose up --build`.  
4. Dev URLs: web `:3000`, API `:8000/docs`, health path.  
5. Pointers to `docs/CLIPPING_ARCHITECTURE.md` and `docs/plans/`.  
6. Phase status: Phase 0 skeleton only; media pipeline in Phase 1.

**Done when:** A new agent can boot the stack from README alone.

---

### Task 0.10 — Smoke verification script (optional but preferred)

**Do:** `scripts/smoke_phase0.sh` (or `make smoke0`) that:
1. Curls health.  
2. Checks Redis ping via API health field or `redis-cli`.  
3. Confirms `data/` writable.

**Done when:** Script exits 0 on a healthy stack.

---

## 4. Parallelization (optional multi-agent)

| Agent A | Agent B |
| --- | --- |
| Tasks 0.1–0.7 (backend, compose, worker) | Task 0.8 (Next.js shell) |
| Then one agent merges CORS/env and writes README (0.9–0.10) | |

Agree on `NEXT_PUBLIC_API_URL` and `/api/v1/health` shape **before** splitting.

---

## 5. Exit criteria (all required)

- [ ] `docker compose up --build` works (or documented local equivalent fully works).  
- [ ] `GET /api/v1/health` returns 200.  
- [ ] Alembic migration applied; DB file under `data/`.  
- [ ] Arq worker processes a ping job → file in `data/`.  
- [ ] Next.js shows health from API.  
- [ ] `.env.example`, `.gitignore`, root README present.  
- [ ] No Phase 1+ features accidentally started.  
- [ ] Update [README.md progress tracker](./README.md) → Phase 0 = `done`.

---

## 6. Handoff notes for Phase 1 agent

Leave these ready:
- Shared `DATA_DIR` layout convention: `data/projects/{project_id}/...` (create in Phase 1).  
- Prefer expanding `projects` model rather than inventing a second schema.  
- Keep API prefix `/api/v1`.  
- Worker entrypoint stable: `arq app.worker.WorkerSettings`.

---

## 7. Agent completion report (fill when done)

```text
Phase 0 complete.
- How to run: ...
- Health URL: ...
- Known gaps: ...
- Next: Phase 1 playbook docs/plans/PHASE_1_MANUAL_CLIPPER.md
```
