#!/usr/bin/env bash
# One-shot Python↔Rust equity-collector A/B on a recorded tape.
#   tools/equity_ab.sh data/equity_tape_raw/2026-08-18.mpk.zst [--start 09:30 --end 09:40]
# Runs the replay server (speed 0), the Rust collector, then the Python collector,
# each into a scratch DB, and diffs them. Prints both collectors' summary lines
# (msgs/s, RSS) and IDENTICAL/DIFFERENT. Scratch dir: $HMT_AB_DIR (default /tmp/hmt_equity_ab).
set -euo pipefail
cd "$(dirname "$0")/.."
TAPE="${1:?tape path}"; shift || true
DIR="${HMT_AB_DIR:-/tmp/hmt_equity_ab}"; mkdir -p "$DIR"; rm -f "$DIR"/py.db* "$DIR"/rs.db* "$DIR"/rs_bars.db*
PORT="${HMT_AB_PORT:-8770}"
RS_BIN="collectors/equity_rs/target/release/equity_rs"
[[ -x "$RS_BIN" ]] || { echo "build first: (cd collectors/equity_rs && cargo build --release)"; exit 2; }
.venv/bin/python tools/equity_db_init.py "$DIR/rs.db" >/dev/null
.venv/bin/python tools/equity_tape_replay.py "$TAPE" --speed 0 --port "$PORT" "$@" >"$DIR/replay.log" 2>&1 &
RP=$!; trap 'kill $RP 2>/dev/null || true' EXIT; sleep 1.5
echo "== rust"
/usr/bin/time -f "  wall=%es cpu=%Us maxrss=%MKB" "$RS_BIN" --replay --url "ws://127.0.0.1:$PORT" --db "$DIR/rs.db" --bars-db "$DIR/rs_bars.db" --proxy-port 8769 2>&1 | grep -E "replay finished|wall=|error|panick" || true
echo "== python"
/usr/bin/time -f "  wall=%es cpu=%Us maxrss=%MKB" .venv/bin/python tools/equity_collector_replay.py --db "$DIR/py.db" --url "ws://127.0.0.1:$PORT" --proxy-port 8768 2>&1 | grep -E "replay finished|wall=" || true
echo "== A/B"
.venv/bin/python tools/equity_tape_ab.py "$DIR/py.db" "$DIR/rs.db"
