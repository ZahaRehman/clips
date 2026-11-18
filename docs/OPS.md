# Cliping operations

Personal one-box runbook for a laptop or a single VPS. Cliping is not a multi-tenant service. Do not publish the URL-ingest routes on the open internet.

## Deploy

SQLite and local disk are the default.

```bash
cp .env.example .env
docker compose up --build
```

| Surface | URL |
| --- | --- |
| Web | http://localhost:3001 |
| API | http://localhost:8000 |
| Health | http://localhost:8000/api/v1/health |

The browser talks to the Next.js app. Next forwards `/api/backend/...` to the API. Inside Compose that upstream is `CLIPING_API_URL=http://api:8000`. On a host process it is `http://localhost:8000`.

Set the same secret in both places when you leave localhost:

```bash
API_TOKEN=replace-with-a-long-random-string
CLIPING_API_TOKEN=replace-with-a-long-random-string
```

`CLIPING_API_TOKEN` is read only by the Next server. It is not a `NEXT_PUBLIC_` variable. With `API_TOKEN` set, every route except `GET /api/v1/health` returns 401 unless the request sends `Authorization: Bearer <token>` or `X-API-Token`. Direct `curl` to the API must send that header. The web UI sends it from the server.

Prefer one of these in front of a VPS, and still set the token:

- Tailscale (or another private network) and bind the published ports to that interface.
- Listen on `127.0.0.1` and reach the box over SSH.
- Caddy or nginx basic auth on the public hostname. Do not expose URL ingest without the token. Leave `ALLOW_URL_INGEST=false` unless you need it.

GPU: install the NVIDIA Container Toolkit on the host and add `gpus: all` under the `worker` service. `WHISPER_DEVICE=auto` uses CUDA when the worker can see it. The web image does not run Whisper.

## Postgres (optional)

SQLite remains the smoke path. To switch:

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

The override file sets `DATABASE_URL=postgresql+asyncpg://cliping:cliping@postgres:5432/cliping` on `api` and `worker`. Change that password before the machine is reachable by anyone else. The API uses asyncpg. The worker uses the same URL rewritten to `postgresql+psycopg`. Alembic runs on container start against whichever URL is set. Migrations use batch alters so the same revisions apply to SQLite and Postgres.

Host install of the drivers is already declared in `services/api/pyproject.toml` (`asyncpg`, `psycopg[binary]`). A local venv needs `pip install -e .` after this phase.

## Storage

`STORAGE_BACKEND=local` (default) reads and writes `DATA_DIR`. Project files go through `app/storage`. `STORAGE_BACKEND=s3` constructs a stub that records `s3://` URLs and refuses reads and writes. The pipeline still needs a local path for FFmpeg, so leave the backend on `local`. `S3_BUCKET`, `S3_REGION`, `S3_PREFIX`, and `S3_ENDPOINT_URL` are reserved and unused.

## Disk and cleanup

Before an upload is stored, and again at ingest, the API checks free space on `DATA_DIR`. It requires `MIN_FREE_BYTES` (default 1 GiB) plus the request size when `Content-Length` is present. Set `MIN_FREE_BYTES=0` to disable the floor. A short disk returns `DISK_FULL`.

Cleanup deletes:

- temp files (`.part`, `.ytdl`, `.tmp`, `.temp`) older than one hour
- `proxy/proxy.mp4` older than `CLEANUP_MAX_AGE_DAYS` when `CLEANUP_DELETE_PROXY=true`
- `proxy/` and `audio/` for projects whose status is `failed` and whose `updated_at` is older than `CLEANUP_MAX_AGE_DAYS`

Raw uploads are kept. The worker runs this at 03:15 and 15:15. On demand:

```bash
curl -X POST http://localhost:8000/api/v1/admin/cleanup \
  -H "Authorization: Bearer $API_TOKEN"
```

The response and the worker log include `bytes_reclaimed`.

## Logs and health

API and worker log one JSON line per pipeline stage: `project_id`, `job_id`, `stage`, `duration_ms`. URLs are rewritten to `[url]` so query tokens do not land in logs. `GET /api/v1/health` adds `jobs_succeeded` and `jobs_failed`, counted in `data/_metrics.json` so the API process can see worker totals. Those counters are for this box, not a metrics product.

Each pipeline job also stores wall seconds per stage in `jobs.checkpoint.timings` (including `proxy_wait`, the time spent waiting for the background 720p proxy). Compare runs with:

```bash
sqlite3 data/cliping.db "select created_at, json_extract(checkpoint,'$.timings') from jobs where kind='pipeline' order by created_at desc limit 10"
```

## Processes

`worker` runs pipelines (default Arq queue). `renderer` runs user-clicked exports (`arq:render`) so an export does not wait behind a chunk pipeline. `fetcher` downloads the next range of a long video (`arq:fetcher`). `publisher` runs YouTube and TikTok uploads (`arq:publish`), so a post goes out while a live stream or a long video is still being clipped. Encode speed settings (`EXPORT_PRESET`, `EXPORT_MAX_FPS`, `PROXY_PRESET`, …) and why they exist are in [PERFORMANCE_RND.md](./PERFORMANCE_RND.md).

## Watermark

Default exports are unchanged. Set one or both:

```bash
WATERMARK_TEXT=Your Name
WATERMARK_IMAGE=/data/brand.png
```

Text is drawn with DejaVu Sans from `FONTS_DIR`. An image is scaled and placed at the lower right. Changing either value changes the export hash, so the next export re-encodes.

## Backup

Copy both of these before an upgrade or a disk move:

- `data/` — SQLite file (`data/cliping.db` by default), project media, `_metrics.json`
- `models/` — Whisper weights. Large. Safe to re-download, slow to re-download.

Postgres: `pg_dump` the `cliping` database as well as `data/` (media is not inside Postgres).

## Models and tools

- First ASR run downloads `WHISPER_MODEL` into `WHISPER_MODEL_DIR` (`models/whisper` in Compose). `medium` is hundreds of MB and can take several minutes.
- Update yt-dlp by rebuilding the API image (`pip` installs it from `pyproject.toml`) or, on the host, upgrading the venv. URL ingest is best-effort. Upload is the path that should keep working.
- Fonts for captions and the text watermark ship in the API image (`FONTS_DIR=/app/app/assets/fonts`).

## Wipe one project

`DELETE /api/v1/projects/{id}` removes the database rows and `data/projects/{id}`. To drop everything local: stop Compose, delete `data/cliping.db` and `data/projects/`, start again. `alembic upgrade head` runs on API/worker start. This does not delete `models/`.

## Security

- Token, Tailscale, or a proxy with auth. Health can stay public.
- Do not put `API_TOKEN` in `NEXT_PUBLIC_` env vars.
- Do not turn on `ALLOW_URL_INGEST` on a public listener.
- File routes only serve files under that project’s directory.

## Soak

A long-source check is `scripts/soak_phase4.sh /path/to/owned.mp4`. It expects a file you have rights to, at least 30 minutes, then ingest, proposals, three exports, and a mid-job restart.

This workspace only had a short upload (about 9 MB) under `data/projects/`. That file is not a 30–120 minute source, so the soak was not run. Run the script on a video you own before treating a VPS as daily-driver.

## Implementation deltas

Relative to `docs/CLIPPING_ARCHITECTURE.md` as of this phase:

- Web calls the API through Next (`/api/backend`) so `CLIPING_API_TOKEN` stays on the server. `CLIPING_API_URL` is the upstream the Next server uses.
- New env: `STORAGE_BACKEND`, `S3_*`, `MIN_FREE_BYTES`, `CLEANUP_MAX_AGE_DAYS`, `CLEANUP_DELETE_PROXY`, `WATERMARK_TEXT`, `WATERMARK_IMAGE`.
- Health JSON includes `jobs_succeeded` and `jobs_failed`.
- Optional Postgres is a second Compose file, not the default service list.
- Remotion was not spiked. See `docs/plans/REMOTION_DEFERRED.md`.
