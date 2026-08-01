#!/usr/bin/env bash
#
# oi_refresh.sh — cron wrapper for the standing-OI top-up (both feeds), with the
# staleness dead-man that the original one-shot builds lacked.
#
# WHY THE DEAD-MAN: data/oi_walls.db fossilized at 2026-07-17 — both halves — and
# nobody noticed for two weeks, because a store that stops updating looks exactly
# like a store with nothing new. So this checks the OUTCOME (age of the newest row
# per source) rather than trusting the fetch's exit code: a fetch that succeeds
# against an empty window is still a dead pipeline. Conversely a transient vendor
# hiccup that leaves the store fresh must NOT page anyone — the next run resumes
# from the newest row and self-heals.
#
# CRONTAB (mbelli):
#   0 17 * * 1-6  /home/mbelli/dev/help_me_trade/tools/oi_refresh.sh >> /home/mbelli/dev/help_me_trade/data/oi_refresh.log 2>&1
#
# 17:00 UTC is 13:00 ET (EDT) / 12:00 ET (EST) — after the measured 10:00-ET final
# settlement pass in either DST regime, so no ET gate is needed. Mon-Sat because
# Friday's settlement publishes Friday evening and Saturday morning UTC.
#
# ENV:
#   NTFY_TOPIC   ntfy.sh topic to POST to on staleness (else stderr only)
#   NTFY_URL     ntfy base URL (default: https://ntfy.sh)
#   STALE_DAYS   escalate when a source's newest row is older than this (default: 4)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
STALE_DAYS="${STALE_DAYS:-4}"

escalate() {
  echo "$1" >&2
  if [[ -n "${NTFY_TOPIC:-}" ]]; then
    curl -fsS -H "Title: OI walls refresh" -H "Priority: high" -H "Tags: warning" \
      -d "$1" "${NTFY_URL%/}/${NTFY_TOPIC}" >/dev/null \
      || echo "oi_refresh: ntfy POST to '$NTFY_TOPIC' failed" >&2
  fi
}

FETCH_RC=0
.venv/bin/python tools/oi_refresh.py "$@" || FETCH_RC=$?

# Outcome check, per source — one feed going stale is invisible in a store-wide max.
ROWS="$(sqlite3 -readonly "$ROOT/data/oi_walls.db" \
  "SELECT source, MAX(date) FROM oi_daily GROUP BY source;" 2>/dev/null || true)"
if [[ -z "$ROWS" ]]; then
  escalate "OI walls: oi_walls.db has no rows at all — the store is empty or unreadable."
  exit 1
fi

STALE=""
SUMMARY=""
while IFS='|' read -r SOURCE NEWEST; do
  [[ -z "$SOURCE" ]] && continue
  AGE=$(( ( $(date -u +%s) - $(date -u -d "$NEWEST" +%s) ) / 86400 ))
  SUMMARY+="${SOURCE}=${NEWEST}(${AGE}d) "
  (( AGE > STALE_DAYS )) && STALE+="${SOURCE} newest ${NEWEST} is ${AGE}d old; "
done <<< "$ROWS"

if [[ -n "$STALE" ]]; then
  escalate "OI walls STALE: ${STALE}limit ${STALE_DAYS}d, fetch rc=${FETCH_RC}. See data/oi_refresh.log."
  exit 1
fi
if (( FETCH_RC != 0 )); then
  echo "oi_refresh: fetch failed (rc=$FETCH_RC) but store is fresh — $SUMMARY; not escalating" >&2
  exit 0
fi
echo "oi_refresh: ok — $SUMMARY"
