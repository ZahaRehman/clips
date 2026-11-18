#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
DATA_DIR="${DATA_DIR_HOST:-$ROOT/data}"

echo "==> health"
health="$(curl -fsS "$API_URL/api/v1/health")"
echo "$health"
echo "$health" | grep -q '"status":"ok"'

echo "==> redis field"
echo "$health" | grep -q '"redis":"ok"'

echo "==> data writable"
mkdir -p "$DATA_DIR"
test_file="$DATA_DIR/_smoke_write.txt"
echo "ok" > "$test_file"
test -f "$test_file"
rm -f "$test_file"

echo "==> db present (after api start/migrate)"
test -f "$DATA_DIR/cliping.db"

echo "Phase 0 smoke OK"
