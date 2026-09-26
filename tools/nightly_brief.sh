#!/usr/bin/env bash
#
# nightly_brief.sh — cron wrapper for tools/nightly_brief.py (the deep brief).
#
# Args pass through to the Python side, except --at-et-hour H: exit 0 unless the
# current America/New_York hour is H. Cron runs in UTC with no reliable CRON_TZ, so
# each job is listed at both its EDT and EST UTC hours and this guard keeps one:
#   30 22,23 * * 0   tools/nightly_brief.sh --at-et-hour 18 --mode weekly
#   30 0,1   * * 2-6 tools/nightly_brief.sh --at-et-hour 20 --mode nightly
# One lock per mode ($TMPDIR, default /tmp), so a long weekly never blocks a nightly.
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

AT_HOUR=""
MODE=nightly
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --at-et-hour) AT_HOUR="${2:-}"; shift 2; continue ;;
    --at-et-hour=*) AT_HOUR="${1#*=}"; shift; continue ;;
    --mode) MODE="${2:-}" ;;
    --mode=*) MODE="${1#*=}" ;;
  esac
  ARGS+=("$1"); shift
done
if [ -n "$AT_HOUR" ]; then
  now_hour=$(TZ=America/New_York date +%H)
  if [ "$((10#$now_hour))" -ne "$((10#$AT_HOUR))" ]; then
    exit 0  # the other DST slot of this cron pair
  fi
fi
LOCK="${TMPDIR:-/tmp}/hmt-brief-${MODE}.lock"

exec 9>"$LOCK"
flock -n 9 || { log "another $MODE brief holds the lock; exiting"; exit 0; }

cd "$ROOT" || alarm "cannot cd $ROOT"

log "brief ${ARGS[*]}"
"$PY" tools/nightly_brief.py "${ARGS[@]}"
rc=$?
case "$rc" in
  0) log "$MODE brief complete" ;;
  2) log "$MODE brief failed (dead-man sent by the brief)"; exit 2 ;;
  *) alarm "$MODE brief exited $rc without its own dead-man" ;;
esac
