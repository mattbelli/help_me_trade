#!/usr/bin/env bash
# serv deploy: restart the hmt systemd services into the current working tree.
# No sudo needed — the units are Restart=always, so killing MainPID makes systemd
# relaunch immediately from the working directory (backend runs migrations + seeds
# on boot; frontend serves the existing production .next build).
#
# Usage: tools/serv-deploy.sh [backend|frontend|both]   (default: both)
# Note: rebuild the frontend (cd frontend && npm run build) BEFORE restarting it.
set -uo pipefail

restart() {
  local svc="$1"
  local pid
  pid=$(systemctl show "$svc" -p MainPID --value 2>/dev/null)
  if [[ -z "$pid" || "$pid" == "0" ]]; then
    echo "[$svc] not running (MainPID=$pid) — skipping"
    return 1
  fi
  echo "[$svc] killing MainPID $pid; systemd will relaunch..."
  kill "$pid"
  sleep 6
  local newpid
  newpid=$(systemctl show "$svc" -p MainPID --value 2>/dev/null)
  if [[ "$newpid" == "$pid" ]]; then
    # SIGTERM ignored (seen 2026-07-18: uvicorn survived TERM during a loop
    # stall) — a same-PID "success" means the OLD code is still serving.
    echo "[$svc] WARNING: PID unchanged after TERM — escalating to SIGKILL"
    kill -9 "$pid"
    sleep 6
    newpid=$(systemctl show "$svc" -p MainPID --value 2>/dev/null)
    if [[ "$newpid" == "$pid" ]]; then
      echo "[$svc] FAILED: still PID $pid after SIGKILL — deploy did NOT land"
      return 1
    fi
  fi
  echo "[$svc] active=$(systemctl is-active "$svc") newMainPID=$newpid (was $pid)"
}

# The collectors ride along with backend deploys — but only if their units
# are installed (optional; see SERVER_SETUP.md "hmt-flow-collector" /
# "hmt-equity-collector").
restart_collector_if_present() {
  for unit in hmt-flow-collector hmt-equity-collector; do
    if systemctl cat "$unit.service" >/dev/null 2>&1; then
      restart "$unit"
    fi
  done
}

target="${1:-both}"

# This script NEVER builds (see header) — so a frontend restart on a .next older
# than the last frontend commit silently re-serves the old bundle as if deployed
# (burned 2026-08-10: three evening "deploys" shipped nothing). Hard-stop that.
if [[ "$target" == "frontend" || "$target" == "both" ]]; then
  root="$(cd "$(dirname "$0")/.." && pwd)"
  build_id="$root/frontend/.next/BUILD_ID"
  if [[ -f "$build_id" ]]; then
    built=$(stat -c %Y "$build_id")
    last_commit=$(git -C "$root" log -1 --format=%ct -- frontend/ 2>/dev/null || echo 0)
    if (( built < last_commit )) && [[ "${ALLOW_STALE:-}" != "1" ]]; then
      echo "FATAL: frontend/.next was built $(date -u -d "@$built" +'%m-%d %H:%M') UTC," >&2
      echo "  but the last commit touching frontend/ is $(date -u -d "@$last_commit" +'%m-%d %H:%M') UTC." >&2
      echo "  Restarting now would serve the OLD bundle. Run first:  cd frontend && npm run build" >&2
      echo "  (override with ALLOW_STALE=1 if serving the old build is intentional)" >&2
      exit 1
    fi
  fi
fi

case "$target" in
  backend)  restart hmt-backend; restart_collector_if_present ;;
  frontend) restart hmt-frontend ;;
  both)     restart hmt-backend; restart hmt-frontend; restart_collector_if_present ;;
  *) echo "usage: $0 [backend|frontend|both]"; exit 2 ;;
esac
