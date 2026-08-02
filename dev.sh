#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv/bin/activate"

if [[ ! -f "$VENV" ]]; then
  echo "Error: missing $VENV — create the venv first (see References/SERVER_SETUP.md):" >&2
  echo "  uv venv --seed --python 3.12 .venv && uv pip install -r requirements.txt" >&2
  exit 1
fi

pids=()

cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap cleanup INT TERM EXIT

echo "Starting dev stack..."
echo "  Backend:  http://127.0.0.1:8000"
echo "  Frontend: http://localhost:3001"

(
  cd "$ROOT"
  # shellcheck source=/dev/null
  source "$VENV"
  exec uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
) &
pids+=($!)

(
  cd "$ROOT/frontend"
  exec npm run dev
) &
pids+=($!)

wait -n
