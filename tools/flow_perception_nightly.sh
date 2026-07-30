#!/usr/bin/env bash
#
# flow_perception_nightly.sh — roll the flow corpus forward, then run the
# perception scan on the fresh session.
#
# Runs from cron after nightly_flow_capture.sh (02:30 UTC) has landed the
# night's tcbbo/defs/condition files. Serialization is by LOCK, not by clock:
# we flock the SAME lock file the capture chain holds, waiting up to 3h, so a
# slow capture never races the rollup and a wedged rollup makes the next
# capture skip loudly instead of double-writing.
#
# Steps (both idempotent, so a missed night self-heals on the next run):
#   1. tools/flow_corpus_rollup.py over a 7-day lookback — skip-existing by
#      default, so only the un-ingested session(s) actually roll (~1 min each).
#   2. tools/flow_perception_scan.py — scans the latest corpus session into
#      flow_perception_daily (upsert; re-running a session replaces its rows).
#
# Failure is LOUD: nonzero exit from either step posts to the ntfy dead-man
# topic — a silently dead scan reads as "nothing unusual today", which is
# exactly the lie the session_age_days stamp exists to prevent.

set -uo pipefail

ROOT=/home/mbelli/dev/help_me_trade
PY="$ROOT/.venv/bin/python"
LOCK=/tmp/hmt-nightly-flow-capture.lock
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-}"

log() { echo "$(date -u +%FT%TZ) $*"; }

alarm() {
  log "FAIL: $*"
  if [ -n "$NTFY_TOPIC" ]; then
    curl -fsS -m 15 -H "Title: flow perception nightly FAILED" -H "Priority: high" \
      -d "$* (host: $(hostname), $(date -u +%FT%TZ))" \
      "$NTFY_URL/$NTFY_TOPIC" >/dev/null || log "ntfy escalation also failed"
  fi
  exit 1
}

exec 9>"$LOCK"
flock -w 10800 9 || alarm "capture chain still holds the lock after 3h"

# Never a second corpus writer: a manual backfill/re-roll owns the corpus.
if pgrep -f "flow_corpus_rollup.py" >/dev/null; then
  log "a rollup process is already running; deferring to it and exiting"
  exit 0
fi

cd "$ROOT" || alarm "cannot cd $ROOT"

# Same 7-day lookback contract as the capture chain: covers holidays and
# missed nights; skip-existing makes the overlap free. At 04:30+ UTC the
# just-closed ET session is yesterday's UTC date, so END=today (exclusive)
# already covers it.
START=$(date -u -d '7 days ago' +%F)
END=$(date -u +%F)

log "corpus rollup $START..$END"
nice -n 10 "$PY" tools/flow_corpus_rollup.py --start "$START" --end "$END" \
  || alarm "corpus rollup failed"

# Forward earnings dates for the corpus universe (exact where a vendor has one,
# EDGAR-cadence estimate otherwise). Incremental — only stale/missing names cost
# a request. NON-FATAL on purpose: a bad EDGAR night degrades a card's earnings
# line to what we already knew, which is nothing like a dead scan reading as
# "nothing unusual today", so it must not skip the scan below.
log "earnings coverage refresh"
nice -n 10 "$PY" tools/earnings_coverage_refresh.py \
  || log "WARN: earnings coverage refresh failed — scan continues on existing coverage"

log "perception scan"
nice -n 10 "$PY" tools/flow_perception_scan.py --top 10 \
  || alarm "perception scan failed"

log "flow perception nightly complete"
