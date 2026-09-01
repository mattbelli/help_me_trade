#!/usr/bin/env bash
#
# tree_guard.sh — the live dev tree must sit on main.
#
# WHY: on 2026-08-26 the tree was left on feat/mobile-app (983 commits behind) for
# 6.5h. 16 of the 17 cron scripts did not exist on that branch, and 14 of them died
# with NO notification — because each job's notifier lives *inside* the script that
# had gone missing. A job cannot report its own absence, so this checks the shared
# PRECONDITION instead of trusting 17 individual dead-men.
#
# CRONTAB (mbelli):
#   */30 * * * *  bash /home/mbelli/dev/help_me_trade/tools/tree_guard.sh >> /home/mbelli/dev/help_me_trade/data/tree_guard.log 2>&1
#
# Invoked via `bash` on purpose: core.fileMode=false here, so the exec bit does not
# survive a fresh checkout.
#
# ENV:
#   NTFY_TOPIC   ntfy.sh topic to POST to when off main (else stderr only)
#   NTFY_URL     ntfy base URL (default: https://ntfy.sh)

set -uo pipefail

ROOT=/home/mbelli/dev/help_me_trade
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-}"

branch=$(git -C "$ROOT" branch --show-current 2>/dev/null)

if [ "$branch" = main ]; then
  exit 0
fi

msg="dev tree is on '${branch:-DETACHED}', not main. All cron jobs and the hmt-* services run from here; scripts that only exist on main are silently missing."
echo "$(date -u +%FT%TZ) tree_guard: $msg"

if [ -n "$NTFY_TOPIC" ]; then
  curl -fsS -m 15 -H "Title: dev tree is OFF MAIN" -H "Priority: urgent" \
    -d "$msg" "$NTFY_URL/$NTFY_TOPIC" >/dev/null
fi
exit 1
