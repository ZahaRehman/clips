#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
SAMPLE="${1:-}"

if [[ -z "$SAMPLE" || ! -f "$SAMPLE" ]]; then
  echo "Usage: $0 /path/to/owned-sample.mp4"
  exit 1
fi

echo "==> upload"
create="$(curl -fsS -F "file=@${SAMPLE}" -F "title=smoke" "$API_URL/api/v1/projects")"
echo "$create"
pid="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$create")"

echo "==> wait ready_for_review ($pid)"
for _ in $(seq 1 600); do
  detail="$(curl -fsS "$API_URL/api/v1/projects/$pid")"
  status="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$detail")"
  echo "status=$status"
  if [[ "$status" == "ready_for_review" ]]; then
    break
  fi
  if [[ "$status" == "failed" ]]; then
    echo "$detail"
    exit 1
  fi
  sleep 2
done

echo "==> create clip 0-10s (adjust if short)"
clip="$(curl -fsS -H 'Content-Type: application/json' \
  -d '{"t_start":0.5,"t_end":8.5,"title":"smoke-clip"}' \
  "$API_URL/api/v1/projects/$pid/clips")"
echo "$clip"
cid="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$clip")"

echo "==> export"
curl -fsS -X POST "$API_URL/api/v1/clips/$cid/export" >/dev/null

for _ in $(seq 1 180); do
  exports="$(curl -fsS "$API_URL/api/v1/clips/$cid/exports")"
  ready="$(python3 -c 'import json,sys; xs=json.load(sys.stdin); print(next((x.get("download_url") or "") for x in xs if x.get("status")=="ready"), "")' <<<"$exports")"
  if [[ -n "$ready" ]]; then
    echo "download: $API_URL$ready"
    echo "Phase 1 smoke OK"
    exit 0
  fi
  sleep 2
done

echo "export timed out"
echo "$exports"
exit 1
