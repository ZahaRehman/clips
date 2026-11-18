#!/usr/bin/env bash
# Upload path with LLM_PROVIDER=none (heuristics). Does not download a URL.
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
SAMPLE="${1:-}"

if [[ -z "$SAMPLE" || ! -f "$SAMPLE" ]]; then
  echo "Usage: $0 /path/to/owned-sample.mp4"
  echo "Requires the API and worker with LLM_PROVIDER=none."
  exit 1
fi

echo "==> upload"
create="$(curl -fsS -F "file=@${SAMPLE}" -F "title=phase2-smoke" "$API_URL/api/v1/projects")"
echo "$create"
pid="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$create")"

echo "==> wait ready_for_review ($pid)"
detail=""
status=""
for _ in $(seq 1 600); do
  detail="$(curl -fsS "$API_URL/api/v1/projects/$pid")"
  status="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$detail")"
  stage="$(python3 -c 'import json,sys; j=(json.load(sys.stdin).get("latest_job") or {}); print(j.get("stage") or "")' <<<"$detail")"
  echo "status=$status stage=$stage"
  if [[ "$status" == "ready_for_review" ]]; then
    break
  fi
  if [[ "$status" == "failed" ]]; then
    echo "$detail"
    exit 1
  fi
  sleep 2
done

if [[ "$status" != "ready_for_review" ]]; then
  echo "timed out waiting for review"
  echo "$detail"
  exit 1
fi

echo "==> proposals"
clips="$(curl -fsS "$API_URL/api/v1/projects/$pid/clips")"
python3 -c '
import json,sys
rows=json.load(sys.stdin)
proposed=[c for c in rows if c.get("status")=="proposed"]
print(f"clips={len(rows)} proposed={len(proposed)}")
for c in proposed[:8]:
    print(f"  {c.get(\"score\")} {c.get(\"t_start\")}–{c.get(\"t_end\")} {c.get(\"title\")}")
'

echo "==> manual clip still works"
clip="$(curl -fsS -H "Content-Type: application/json" \
  -d '{"t_start":0.5,"t_end":8.5,"title":"phase2-manual"}' \
  "$API_URL/api/v1/projects/$pid/clips")"
echo "$clip"
echo "Phase 2 upload smoke OK"
