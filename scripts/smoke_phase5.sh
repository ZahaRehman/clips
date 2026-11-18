#!/usr/bin/env bash
# Usage: scripts/smoke_phase5.sh [owned-url-or-file]
# Without an argument only the unit tests run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
TIMEOUT_SEC="${SMOKE_TIMEOUT_SEC:-7200}"
SOURCE="${1:-}"
AUTH=()
if [[ -n "${API_TOKEN:-}" ]]; then
  AUTH=(-H "Authorization: Bearer $API_TOKEN")
fi

echo "==> phase 5 unit tests"
(
  cd "$ROOT/services/api"
  .venv/bin/python -m unittest tests.test_phase5
)

if [[ -z "$SOURCE" ]]; then
  echo "No source given — unit tests passed. Pass an owned file or URL to run autopilot end to end."
  exit 0
fi

curl -fsS --max-time 5 "$API_URL/api/v1/health" >/dev/null

echo "==> create autopilot project from $SOURCE"
if [[ -f "$SOURCE" ]]; then
  created="$(curl -fsS "${AUTH[@]}" -X POST "$API_URL/api/v1/projects" \
    -F "file=@$SOURCE" -F autopilot=true -F clip_count=10)"
else
  created="$(curl -fsS "${AUTH[@]}" -X POST "$API_URL/api/v1/projects" \
    -H 'Content-Type: application/json' \
    -d "{\"url\":\"$SOURCE\",\"disclaimer_accepted\":true,\"autopilot\":true,\"clip_count\":10}")"
fi
project_id="$(echo "$created" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
echo "project $project_id"

started=$(date +%s)
while true; do
  status="$(curl -fsS "${AUTH[@]}" "$API_URL/api/v1/projects/$project_id" \
    | python3 -c 'import json,sys; p=json.load(sys.stdin); j=p.get("latest_job") or {}; print(p["status"], j.get("stage"), round(j.get("progress") or 0, 2))')"
  echo "  $status"
  case "$status" in
    ready_for_review*) break ;;
    failed*) echo "pipeline failed"; exit 1 ;;
  esac
  if (( $(date +%s) - started > TIMEOUT_SEC )); then
    echo "timed out after ${TIMEOUT_SEC}s"
    exit 1
  fi
  sleep 10
done
echo "wall time: $(( $(date +%s) - started ))s"

echo "==> check rendered clips"
curl -fsS "${AUTH[@]}" "$API_URL/api/v1/projects/$project_id" \
  | python3 -c 'import json,sys; print("summary:", json.dumps(json.load(sys.stdin).get("autopilot_summary"), indent=2))'
curl -fsS "${AUTH[@]}" "$API_URL/api/v1/projects/$project_id/clips" | python3 -c '
import json, sys
clips = [c for c in json.load(sys.stdin) if c["status"] != "rejected"]
ready = [c for c in clips if (c.get("latest_export") or {}).get("status") == "ready"]
assert ready, "no ready exports"
windows = []
for c in ready:
    e = c["latest_export"]
    d = e["duration_sec"] or 0
    assert (e["width"], e["height"]) == (1080, 1920), f"{c['id']}: {e['width']}x{e['height']}"
    assert 19.9 <= d <= 59.5, f"{c['id']}: duration {d}"
    assert (c.get("post_title") or "").strip(), f"{c['id']}: empty post_title"
    windows.append((c["t_start"], c["t_end"]))
windows.sort()
for (s1, e1), (s2, e2) in zip(windows, windows[1:]):
    assert s2 >= e1 - 0.05, f"overlap {s1}-{e1} and {s2}-{e2}"
for c in ready:
    print(f"  #{c['rank']:>2} {c['latest_export']['duration_sec']:.1f}s [{c['post_meta_source']}] {c['post_title']}")
print(f"{len(ready)} ready exports, all 1080x1920, 20-59s, non-overlapping, titled")
'
echo "Phase 5 smoke OK"
