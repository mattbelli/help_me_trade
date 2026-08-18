#!/usr/bin/env bash
# One-shot Python↔Rust flow-collector A/B on an archived OPRA tcbbo session.
#   tools/flow_ab.sh 2026-08-17 [--start 09:30 --end 09:40]
# Runs the Rust collector then the Python collector off the same
# data/databento/opra_tcbbo/<date>.dbn.zst + opra_defs/<date>.dbn.zst, each into
# its own sandbox dir, and diffs them (tools/flow_ab.py). Prints both summary
# lines (rec/s, RSS) and IDENTICAL/DIFFERENT. Scratch: $HMT_AB_DIR (default /tmp/hmt_flow_ab).
# Half days: pass --early-close (Python reads the calendar itself; Rust needs the flag).
set -euo pipefail
cd "$(dirname "$0")/.."
DATE="${1:?session date YYYY-MM-DD}"; shift || true
DIR="${HMT_AB_DIR:-/tmp/hmt_flow_ab}"; rm -rf "$DIR/py" "$DIR/rs"; mkdir -p "$DIR/py" "$DIR/rs"
RS_BIN="collectors/flow_rs/target/release/flow_rs"
[[ -x "$RS_BIN" ]] || { echo "build first: (cd collectors/flow_rs && cargo build --release)"; exit 2; }
TAPE="data/databento/opra_tcbbo/$DATE.dbn.zst"; DEFS="data/databento/opra_defs/$DATE.dbn.zst"
RS_ARGS=(); PY_ARGS=()
for a in "$@"; do
  case "$a" in
    --early-close) RS_ARGS+=("$a") ;;
    *) RS_ARGS+=("$a"); PY_ARGS+=("$a") ;;
  esac
done
.venv/bin/python tools/flow_db_init.py "$DIR/rs/trading.db" >/dev/null
echo "== rust"
/usr/bin/time -f "  wall=%es cpu=%Us maxrss=%MKB" "$RS_BIN" --date "$DATE" --tape "$TAPE" --defs "$DEFS" --db "$DIR/rs/trading.db" "${RS_ARGS[@]}" 2>&1 | grep -E "replay finished|wall=|rror|panick" || true
echo "== python"
/usr/bin/time -f "  wall=%es cpu=%Us maxrss=%MKB" .venv/bin/python tools/flow_collector_replay.py "$DATE" --db "$DIR/py/trading.db" "${PY_ARGS[@]}" 2>&1 | grep -E "replay finished|wall=|Traceback|rror" || true
echo "== A/B"
.venv/bin/python tools/flow_ab.py "$DIR/py" "$DIR/rs"
