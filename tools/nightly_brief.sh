#!/usr/bin/env bash
#
# nightly_brief.sh — cron wrapper for tools/nightly_brief.py (the nightly deep brief).
#
# The Python side sends its own ntfy dead-man and exits 2 on a handled failure
# (empty board, judge failed, caught crash). Any OTHER nonzero exit means it died
# before it could say so (import error, OOM kill), so this wrapper alarms.
#
# Cron-cd trap: cd to ROOT after the lock. Exec-bit required (git fileMode=false
# drops it; chmod +x on the box).

set -uo pipefail

ROOT=/home/mbelli/dev/help_me_trade
PY="$ROOT/.venv/bin/python"
LOCK=/tmp/hmt-nightly-brief.lock
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-}"
export PATH="/home/mbelli/.local/bin:$PATH"  # the claude CLI

log() { echo "$(date -u +%FT%TZ) $*"; }

alarm() {
  log "FAIL: $*"
  if [ -n "$NTFY_TOPIC" ]; then
    curl -fsS -m 15 -H "Title: nightly brief FAILED" -H "Priority: high" \
      -d "$* (host: $(hostname), $(date -u +%FT%TZ))" \
      "$NTFY_URL/$NTFY_TOPIC" >/dev/null || log "ntfy escalation also failed"
  fi
  exit 1
}

exec 9>"$LOCK"
flock -n 9 || { log "another nightly brief holds the lock; exiting"; exit 0; }

cd "$ROOT" || alarm "cannot cd $ROOT"

log "nightly brief $*"
"$PY" tools/nightly_brief.py "$@"
rc=$?
case "$rc" in
  0) log "nightly brief complete" ;;
  2) log "nightly brief failed (dead-man sent by the brief)"; exit 2 ;;
  *) alarm "nightly brief exited $rc without its own dead-man" ;;
esac
