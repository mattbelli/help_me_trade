#!/usr/bin/env bash
#
# idea_engine_deadman.sh — external liveness dead-man for the Idea Engine canary.
#
# WHY THIS EXISTS (outside-the-failure-domain guarantee): the in-process auditor
# (signals/pipeline_audit.py) fires a critical page when the daily canary push
# leaves no push_deliveries receipt. But if hmt-backend itself is dead — wedged
# event loop, crashed process, host swap-thrash — that auditor cannot page,
# because it is inside the thing that died. This cron runs INDEPENDENTLY of
# hmt-backend: it reads the trading DB directly with the sqlite3 CLI (read-only)
# and escalates on a channel that does NOT traverse the app's push pipeline.
#
# MUST NOT traverse hmt-backend: no HTTP to the app, no `python -c "import ..."`
# of app modules, no dependency on the app process being up. Only the sqlite3
# CLI plus an independent notification channel.
#
# WHAT IT CHECKS: today's canary writes push_deliveries row proposal_id
# "canary:<ET-date>" status='sent' when its push round-trips. By 16:45 ET that
# receipt must exist; its absence means the canary never launched, never pushed,
# or the transport is dead — all of which are down-signals the founder must see.
#
# ESCALATION CHANNEL [ADR-5]: independent of the backend and its push pipeline.
#   1. ntfy.sh topic  — if NTFY_TOPIC is set (preferred; phone push, no backend)
#   2. mail(1)        — if present (MAILTO or local user mailbox)
#   3. neither        — exit 2 with a LOUD message; an unconfigured dead-man is
#                       itself a fault and must not fail silently.
#
# CRONTAB (install under the mbelli user — `crontab -e`). Both UTC times bracket
# EST/EDT; the script gates on ET internally so only the 16:xx-ET invocation acts
# (the sampler-cron DST lesson — journal/cron clock is UTC, the market is ET):
#
#   45 20 * * 1-5  /home/mbelli/dev/help_me_trade/tools/idea_engine_deadman.sh
#   45 21 * * 1-5  /home/mbelli/dev/help_me_trade/tools/idea_engine_deadman.sh
#
# ENV:
#   TRADING_DB_PATH  path to trading.db (default: <repo>/data/trading.db)
#   NTFY_TOPIC       ntfy.sh topic to POST to on escalation (preferred channel)
#   NTFY_URL         ntfy base URL (default: https://ntfy.sh)
#   MAILTO           mail(1) recipient on the fallback channel (default: mbelli)
# FLAGS:
#   --check-only     run the query + print status, NEVER escalate, always exit 0
#                    (the deploy-time dry-run: proves DB reach + query shape)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${TRADING_DB_PATH:-$ROOT/data/trading.db}"
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
fi

# ET wall clock — TZ override is scoped to each subshell, never exported.
ET_DATE="$(TZ=America/New_York date +%Y-%m-%d)"
ET_HOUR="$(TZ=America/New_York date +%H)"
ET_DOW="$(TZ=America/New_York date +%u)"  # 1=Mon .. 7=Sun

# Gate on ET (skip for the dry-run, which must work at any hour on any day).
if [[ "$CHECK_ONLY" -eq 0 ]]; then
  if [[ "$ET_DOW" -ge 6 ]]; then
    echo "deadman: ET weekend ($ET_DATE) — no canary expected, exiting"
    exit 0
  fi
  if [[ "$ET_HOUR" != "16" ]]; then
    echo "deadman: ET hour is $ET_HOUR, not 16 — this is the off-DST cron invocation, exiting"
    exit 0
  fi
fi

CANARY_ID="canary:${ET_DATE}"

RECEIPT=""
if [[ -r "$DB_PATH" ]]; then
  RECEIPT="$(sqlite3 -readonly "$DB_PATH" \
    "SELECT 1 FROM push_deliveries WHERE proposal_id='${CANARY_ID}' AND status='sent' LIMIT 1;" \
    2>/dev/null || true)"
else
  echo "deadman: trading DB not readable at $DB_PATH" >&2
  # An unreadable DB is itself a fault — fall through to escalation.
fi

if [[ -n "$RECEIPT" ]]; then
  echo "deadman: canary receipt present for $CANARY_ID — Idea Engine alive"
  exit 0
fi

ALERT="IDEA ENGINE DEAD-MAN: no canary push receipt for ${CANARY_ID} by 16:45 ET. \
The in-process auditor/canary or the push transport is down — check hmt-backend \
and the push pipeline."
echo "$ALERT" >&2

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "deadman: --check-only — not escalating (receipt absent, which is normal off-schedule)"
  exit 0
fi

# --- Escalate on a channel independent of hmt-backend ------------------------
if [[ -n "${NTFY_TOPIC:-}" ]]; then
  if curl -fsS \
      -H "Title: Idea Engine dead-man" \
      -H "Priority: urgent" \
      -H "Tags: rotating_light" \
      -d "$ALERT" \
      "${NTFY_URL%/}/${NTFY_TOPIC}" >/dev/null; then
    echo "deadman: escalated via ntfy topic '$NTFY_TOPIC'"
    exit 1
  fi
  echo "deadman: ntfy POST to '$NTFY_TOPIC' failed" >&2
fi

if command -v mail >/dev/null 2>&1; then
  if printf '%s\n' "$ALERT" | mail -s "Idea Engine dead-man" "${MAILTO:-mbelli}"; then
    echo "deadman: escalated via mail(1) to '${MAILTO:-mbelli}'"
    exit 1
  fi
  echo "deadman: mail(1) send failed" >&2
fi

echo "deadman: NO ESCALATION CHANNEL CONFIGURED — set NTFY_TOPIC or install mail(1). \
The canary receipt is MISSING and this alert could not be delivered." >&2
exit 2
