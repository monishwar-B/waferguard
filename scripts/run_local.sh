#!/usr/bin/env bash
# Run from source without Docker (SQLite, in-process queue):  ./scripts/run_local.sh  ->  http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -q -r requirements.txt
exec python3 -m uvicorn waferguard.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
