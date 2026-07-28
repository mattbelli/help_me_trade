#!/usr/bin/env bash
#
# nightly_flow_capture.sh — forward capture for the whole-market flow corpus.
#
# The thin implementation of FLOW_PACKAGE_ORIENTATION_DESIGN §6b "forward" mode
# (decided 2026-07-28: REST after close, NOT a wildcard-WS daemon — the REST tape
# is complete by construction, so the gap-accounting §6b mandates for a WS capture
# never arises; a WS daemon only earns its keep when something consumes condition
# codes INTRADAY, and no such consumer exists — all are gated behind roadmap 1.9).
#
# Chain, in dependency order (every step skip-existing/resumable, so the whole
# script is idempotent and a missed night self-heals on the next run):
#   1. Databento tcbbo download   — the free 12-month window ROLLS DAILY; a session
#      not captured before it ages out becomes a purchase. --max-cost 0.0 keeps
#      this unattended run deny-by-default on spend: any nonzero quote aborts.
#   2. Databento definition download — same window, resolves instrument→contract.
#   3. Alpaca condition-code capture — the labels (Addendum E ground truth).
#   4. Label-join sweep — appends the new session to join_ledger.json (the v6
#      migration gate) and prints its floor verdict into the log.
#
# Failure is LOUD: any step's nonzero exit posts to the ntfy dead-man topic,
# because a silently dead capture cron costs real dollars as the window rolls.
#
# Runs from cron (UTC box) at 02:30 UTC Tue–Sat = 22:30 ET Mon–Fri, after the
# evening's session is available on both vendors (tcbbo verified fetchable 20:58 ET
# same day). Never runs alongside a manual backfill: pgrep guard below.

set -uo pipefail

ROOT=/home/mbelli/dev/help_me_trade
PY="$ROOT/.venv/bin/python"
DATA="$ROOT/data/databento"
NPZ="$ROOT/data/alpaca_conditions"
LOCK=/tmp/hmt-nightly-flow-capture.lock
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-}"

log() { echo "$(date -u +%FT%TZ) $*"; }

alarm() {
  log "FAIL: $*"
  if [ -n "$NTFY_TOPIC" ]; then
    curl -fsS -m 15 -H "Title: nightly flow capture FAILED" -H "Priority: high" \
      -d "$* (host: $(hostname), $(date -u +%FT%TZ))" \
      "$NTFY_URL/$NTFY_TOPIC" >/dev/null || log "ntfy escalation also failed"
  fi
  exit 1
}

exec 9>"$LOCK"
flock -n 9 || { log "another nightly capture holds the lock; exiting"; exit 0; }

# The long historical backfills write the same output dirs — never two writers.
if pgrep -f "alpaca_condition_backfill.py|opra_tcbbo_backfill.py" >/dev/null; then
  log "a backfill process is already running; deferring to it and exiting"
  exit 0
fi

cd "$ROOT" || alarm "cannot cd $ROOT"

# 7-day lookback: covers holidays, missed nights, and vendor-availability lag;
# skip-existing makes the overlap free. End is exclusive = today (UTC): at
# 02:30 UTC the just-closed ET session is yesterday's UTC date, so "today"
# already covers it — "tomorrow" would request a session that hasn't traded
# yet (422 data_start_after_available_end; first firing 2026-07-28 proved it).
START=$(date -u -d '7 days ago' +%F)
END=$(date -u +%F)

log "tcbbo download $START..$END"
nice -n 10 "$PY" tools/opra_tcbbo_backfill.py --start "$START" --end "$END" \
  --schema tcbbo --max-cost 0.0 || alarm "tcbbo download failed"

log "definition download $START..$END"
nice -n 10 "$PY" tools/opra_tcbbo_backfill.py --start "$START" --end "$END" \
  --schema definition --max-cost 0.0 || alarm "definition download failed"

log "alpaca condition capture"
nice -n 10 "$PY" tools/alpaca_condition_backfill.py \
  --data-dir "$DATA" --out-dir "$NPZ" --env-file "$ROOT/.env" \
  || alarm "condition capture failed"

log "label-join sweep"
nice -n 10 "$PY" tools/condition_label_join.py --sweep \
  --data-dir "$DATA" --npz-dir "$NPZ" || alarm "label-join sweep failed"

log "nightly flow capture complete"
