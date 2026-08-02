#!/usr/bin/env bash
#
# edgar_nightly_refresh.sh — nightly EDGAR companyfacts top-up: a CRC diff of
# SEC's zip central directory over HTTP Range, ingesting only the entries that
# moved (see research/edgar_refresh.py for the measurements behind the design).
#
# WHY IT EXISTS: nothing refreshed the companyfacts corpus on a schedule.
# Measured 2026-08-02, AAPL's newest facts were 2026-05-01 — its fiscal-Q3 10-Q
# was simply absent — while 10-Q/10-K land 0-3 days after the earnings release.
#
# THE DEAD-MAN: like tools/oi_refresh.sh, the check is on the OUTCOME, not on the
# exit code alone. "Checked, nothing new" is the normal night — 97.7% of entries
# are byte-identical — and it must stay quiet. "Could not check" is an error and
# must be loud. The ingest watermark is what tells them apart: the job stamps
# checked_at for EVERY filer it verified, changed or not, so a stale
# MAX(checked_at) means the job itself has stopped. No count of facts could
# reveal that, because a healthy night writes almost none.
#
# ONE-TIME SETUP, before the first cron run:
#   .venv/bin/python -m research.edgar_refresh --seed-from
# which records the entry CRCs of the companyfacts.zip we already ingested. Skip
# it and the first run sees an empty ledger, calls all ~20k entries changed, and
# does a full rebuild instead of a diff. It stamps no checked_at — those filers
# still read back as "never checked" until a real run verifies them.
#
# CRONTAB (mbelli):
#   10 7 * * * NTFY_TOPIC=hmt-deadman-0033ce1c5edb753f9932 /home/mbelli/dev/help_me_trade/tools/edgar_nightly_refresh.sh >> /home/mbelli/dev/help_me_trade/data/research/edgar_refresh.log 2>&1
#
# 07:10 UTC = 03:10 ET (EDT) / 02:10 ET (EST): overnight, hours before the open.
# The slot is set from the snapshot's own header, not a guess — measured
# 2026-08-02, companyfacts.zip carried Last-Modified 04:38:46 UTC, so 07:10
# leaves ~2.5h of margin on SEC's regeneration. That is ONE observation, so the
# job logs Last-Modified on every run and the slot can be retuned from the log.
# Being early is self-healing anyway (the diff finds nothing and the next run
# catches up); being late costs nothing.
# Clear of the 03:00 db backup, the 04:30 flow-perception roll, the 06:00 Schwab
# reauth, Sunday's 08:00 DERA top-up and the 09:15 external backup — none of
# which should share a window with a multi-hundred-MB fetch.
#
# ENV:
#   NTFY_TOPIC   ntfy.sh topic to POST to on failure (else stderr only)
#   NTFY_URL     ntfy base URL (default: https://ntfy.sh)
#   STALE_DAYS   escalate when the newest watermark is older than this (default: 3)
#
# Idempotent: a second run in the same night diffs against the CRCs the first one
# recorded, finds nothing changed, and re-stamps checked_at.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
STALE_DAYS="${STALE_DAYS:-3}"

escalate() {
  echo "$1" >&2
  if [[ -n "${NTFY_TOPIC:-}" ]]; then
    curl -fsS -H "Title: EDGAR companyfacts refresh" -H "Priority: high" -H "Tags: warning" \
      -d "$1" "${NTFY_URL%/}/${NTFY_TOPIC}" >/dev/null \
      || echo "edgar_nightly_refresh: ntfy POST to '$NTFY_TOPIC' failed" >&2
  fi
}

RC=0
.venv/bin/python -m research.edgar_refresh --run "$@" || RC=$?

NEWEST="$(sqlite3 -readonly "$ROOT/data/research/edgar_facts.db" \
  "SELECT MAX(checked_at) FROM edgar_ingest_watermark;" 2>/dev/null || true)"

if [[ -z "$NEWEST" ]]; then
  escalate "EDGAR refresh: no ingest watermark rows at all — no run has ever completed a check (rc=$RC)."
  exit 1
fi

# The job runs nightly and stamps every verified filer, so a healthy watermark is
# always <24h old; 3 days is two missed runs, not a quiet weekend.
AGE=$(( ( $(date -u +%s) - $(date -u -d "$NEWEST" +%s) ) / 86400 ))
if (( AGE > STALE_DAYS )); then
  escalate "EDGAR refresh STALE: newest SEC check ${NEWEST} is ${AGE}d old (limit ${STALE_DAYS}d, rc=${RC})."
  exit 1
fi
if (( RC != 0 )); then
  escalate "EDGAR refresh FAILED (rc=${RC}) — could not check SEC. Newest watermark ${NEWEST} (${AGE}d). See data/research/edgar_refresh.log."
  exit 1
fi
echo "edgar_nightly_refresh: ok — newest SEC check ${NEWEST} (${AGE}d)"
