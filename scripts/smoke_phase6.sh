#!/usr/bin/env bash
# Usage: scripts/smoke_phase6.sh
# Unit tests always. If the API is up, also checks YouTube status JSON.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
AUTH=()
if [[ -n "${API_TOKEN:-}" ]]; then
  AUTH=(-H "Authorization: Bearer $API_TOKEN")
fi

echo "==> phase 6 unit tests"
(
  cd "$ROOT/services/api"
  .venv/bin/python -m unittest tests.test_phase6
)

if curl -fsS --max-time 5 "$API_URL/api/v1/health" >/dev/null 2>&1; then
  echo "==> youtube status"
  curl -fsS --max-time 10 "${AUTH[@]}" "$API_URL/api/v1/youtube/status" \
    | python3 -c 'import json,sys; body=json.load(sys.stdin); assert "configured" in body and "connected" in body; print("configured", body["configured"], "connected", body["connected"])'
else
  echo "API not reachable — skipped live status check."
fi
