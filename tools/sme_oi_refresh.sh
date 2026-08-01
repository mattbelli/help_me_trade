#!/usr/bin/env bash
#
# sme_oi_refresh.sh — cron wrapper for the SME open-interest top-up, with the
# staleness dead-man that was missing the first time.
#
# WHY THE DEAD-MAN: data/oi_walls.db fossilized at 2026-07-17 and nobody noticed
# for two weeks, because a store that stops updating looks exactly like a store
# with nothing new. So this checks the OUTCOME (how old is the newest SME row)
# rather than trusting the exit code of the fetch — a fetch that succeeds while
# Databento serves an empty window is still a dead pipeline.
#
# CRONTAB (mbelli):
#   0 17 * * 1-6  /home/mbelli/dev/help_me_trade/tools/sme_oi_refresh.sh >> /home/mbelli/dev/help_me_trade/data/sme_oi_refresh.log 2>&1
#
# 17:00 UTC is 13:00 ET (EDT) / 12:00 ET (EST) — after the 10:00-ET final
# settlement pass in either DST regime, so no ET gate is needed. Mon-Sat because
# Friday's settlement publishes Friday evening and Saturday morning UTC.
#
# ENV:
#   NTFY_TOPIC   ntfy.sh topic to POST to on failure or staleness (else stderr only)
#   NTFY_URL     ntfy base URL (default: https://ntfy.sh)
#   STALE_DAYS   escalate when the newest SME row is older than this (default: 4)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
STALE_DAYS="${STALE_DAYS:-4}"

escalate() {
  echo "$1" >&2
  if [[ -n "${NTFY_TOPIC:-}" ]]; then
    curl -fsS -H "Title: SME OI refresh" -H "Priority: high" -H "Tags: warning" \
      -d "$1" "${NTFY_URL%/}/${NTFY_TOPIC}" >/dev/null \
      || echo "sme_oi_refresh: ntfy POST to '$NTFY_TOPIC' failed" >&2
  fi
}

FETCH_RC=0
.venv/bin/python tools/sme_oi_refresh.py "$@" || FETCH_RC=$?

# Escalate on the OUTCOME, never on the attempt. A transient Databento hiccup that
# leaves the store fresh is not worth a phone alert — tomorrow's run self-heals,
# because the window always resumes from the newest stored row. Only a store that
# has actually stopped advancing wakes anyone up.
NEWEST="$(sqlite3 -readonly "$ROOT/data/oi_walls.db" \
  "SELECT MAX(date) FROM oi_daily WHERE source='sme';" 2>/dev/null || true)"
if [[ -z "$NEWEST" ]]; then
  escalate "SME OI: oi_walls.db has NO sme rows at all — the store is empty or unreadable."
  exit 1
fi
AGE=$(( ( $(date -u +%s) - $(date -u -d "$NEWEST" +%s) ) / 86400 ))
if (( AGE > STALE_DAYS )); then
  escalate "SME OI is STALE: newest row $NEWEST is ${AGE}d old (limit ${STALE_DAYS}d, fetch rc=${FETCH_RC}). The collar legs in oi_walls.db are not advancing — see data/sme_oi_refresh.log."
  exit 1
fi
if (( FETCH_RC != 0 )); then
  echo "sme_oi_refresh: fetch failed (rc=$FETCH_RC) but store is fresh — newest $NEWEST (${AGE}d); not escalating" >&2
  exit 0
fi
echo "sme_oi_refresh: ok — newest SME row $NEWEST (${AGE}d old)"
