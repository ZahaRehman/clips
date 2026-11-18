#!/usr/bin/env bash
# Long owned source: ingest → proposals → accept → export at least 3 clips.
# Restart the worker once while a job is running, then POST reprocess and confirm it finishes.
# Usage: ./scripts/soak_phase4.sh /path/to/owned.mp4
set -euo pipefail

FILE="${1:-}"
API_URL="${API_URL:-http://localhost:8000}"
MIN_SEC="${MIN_SEC:-1800}"

if [[ -z "$FILE" || ! -f "$FILE" ]]; then
  echo "Pass a video you own: ./scripts/soak_phase4.sh /path/to/owned.mp4" >&2
  exit 2
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe is not on PATH. Install FFmpeg, then re-run." >&2
  exit 2
fi

duration="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$FILE")"
python3 - "$duration" "$MIN_SEC" <<'PY'
import sys
duration = float(sys.argv[1] or 0)
minimum = float(sys.argv[2])
if duration < minimum:
    sys.stderr.write(f"Source is {duration:.0f}s. Soak needs at least {minimum:.0f}s (30 minutes).\n")
    sys.exit(2)
print(f"duration_sec={duration:.0f}")
PY

AUTH=()
if [[ -n "${API_TOKEN:-}" ]]; then
  AUTH=(-H "Authorization: Bearer $API_TOKEN")
fi

echo "==> upload"
create="$(curl -fsS "${AUTH[@]}" -F "file=@${FILE}" "$API_URL/api/v1/projects")"
echo "$create"
id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$create")"

echo "==> wait until ready_for_review or failed"
echo "While this runs, restart the worker once (docker compose restart worker), then:"
echo "  curl -X POST $API_URL/api/v1/projects/$id/reprocess -H 'Content-Type: application/json' -d '{\"from_stage\":\"ingest\"}'"

for _ in $(seq 1 720); do
  body="$(curl -fsS "${AUTH[@]}" "$API_URL/api/v1/projects/$id")"
  status="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$body")"
  echo "status=$status"
  if [[ "$status" == "failed" ]]; then
    echo "$body"
    exit 1
  fi
  if [[ "$status" == "ready_for_review" || "$status" == "done" ]]; then
    break
  fi
  sleep 10
done

echo "==> accept and export the first 3 proposals"
python3 - "$API_URL" "$id" <<'PY'
import json, os, sys, urllib.request
api, project = sys.argv[1], sys.argv[2]
token = os.environ.get("API_TOKEN", "")
def call(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(api + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else None
clips = call("GET", f"/api/v1/projects/{project}/clips")
if len(clips) < 3:
    sys.stderr.write(f"Need at least 3 clips, got {len(clips)}\n")
    sys.exit(1)
for clip in clips[:3]:
    if clip["status"] != "accepted":
        call("PATCH", f"/api/v1/clips/{clip['id']}", {"status": "accepted"})
    call("POST", f"/api/v1/clips/{clip['id']}/export")
print("queued=3")
PY

echo "Poll GET /api/v1/projects/$id/clips until three exports are ready, and record wall time in docs/OPS.md."
echo "Soak upload queued for $id"
