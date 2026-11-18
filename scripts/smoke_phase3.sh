#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/services/api"
PY="${ROOT}/services/api/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY=python3
fi
"$PY" -m unittest tests.test_phase3
"$PY" "$ROOT/scripts/check_captions.py"
echo "phase 3 smoke ok"
