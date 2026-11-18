#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"

echo "==> phase 4 unit tests"
(
  cd "$ROOT/services/api"
  .venv/bin/python -m unittest tests.test_phase4
)

echo "==> live API (skipped if it is not up)"
if ! health="$(curl -fsS --max-time 3 "$API_URL/api/v1/health" 2>/dev/null)"; then
  echo "API not running at $API_URL — unit tests passed"
  exit 0
fi

echo "$health"
echo "$health" | grep -q '"jobs_succeeded"'
echo "$health" | grep -q '"jobs_failed"'

if [[ -n "${API_TOKEN:-}" ]]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' "$API_URL/api/v1/projects")"
  test "$code" = "401"
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $API_TOKEN" "$API_URL/api/v1/projects")"
  test "$code" != "401"
fi

echo "Phase 4 smoke OK"
