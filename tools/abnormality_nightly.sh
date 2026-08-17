#!/usr/bin/env bash
#
# abnormality_nightly.sh — roll the 5-min + daily equity-bar stores, rebuild
# baselines, grade yesterday's fires.
#
# Independent of the flow-capture lock: this chain talks to Alpaca SIP bars,
# not the tcbbo corpus. Own flock so a wedged run doesn't stack.
#
# Cron-cd trap: we cd to ROOT after taking the lock. Exec-bit required.

set -uo pipefail

ROOT=/home/mbelli/dev/help_me_trade
PY="$ROOT/.venv/bin/python"
LOCK=/tmp/hmt-abnormality-nightly.lock
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-}"

log() { echo "$(date -u +%FT%TZ) $*"; }

alarm() {
  log "FAIL: $*"
  if [ -n "$NTFY_TOPIC" ]; then
    curl -fsS -m 15 -H "Title: abnormality nightly FAILED" -H "Priority: high" \
      -d "$* (host: $(hostname), $(date -u +%FT%TZ))" \
      "$NTFY_URL/$NTFY_TOPIC" >/dev/null || log "ntfy escalation also failed"
  fi
  exit 1
}

exec 9>"$LOCK"
flock -n 9 || { log "another abnormality nightly holds the lock; exiting"; exit 0; }

cd "$ROOT" || alarm "cannot cd $ROOT"

log "5-min bars --universe alpaca --since-last"
nice -n 10 "$PY" tools/equity_bars_5min_backfill.py --universe alpaca --since-last \
  || alarm "5-min backfill failed"

log "daily bars --since-last"
nice -n 10 "$PY" tools/equity_bars_daily_backfill.py --since-last \
  || alarm "daily backfill failed"

# Grade today's fires BEFORE the rebuild: the forward-norm denominator must be
# built from sessions <= t-1 (the registered no-same-day-leakage rule).
log "outcome sweep"
nice -n 10 "$PY" tools/abnormality_outcomes.py \
  || alarm "outcome sweep failed"

log "baselines rebuild"
nice -n 10 "$PY" tools/abnormality_baselines.py \
  || alarm "baselines rebuild failed"

log "abnormality nightly complete"
