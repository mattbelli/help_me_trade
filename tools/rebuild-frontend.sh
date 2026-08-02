#!/usr/bin/env bash
#
# Rebuild the Next.js frontend and restart its systemd service as ONE atomic step.
#
# Why this exists: the frontend runs in production via `next start` (systemd unit
# hmt-frontend), which loads the `.next` build into memory ONCE at boot. If anyone
# (you, or Composer) runs `npm run build` without restarting the service, the live
# server keeps serving the old module graph against freshly-overwritten chunks ->
# hydration fails -> /workspace (and every page) goes blank/white.
#
# ALWAYS rebuild the running frontend through this script. Never `npm run build`
# the live deployment by itself.
#
# Usage:  tools/rebuild-frontend.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="hmt-frontend"

echo "==> Building frontend (.next)..."
cd "$ROOT/frontend"
npm run build

echo "==> Restarting $SERVICE (needs sudo)..."
sudo systemctl restart "$SERVICE"

# Give it a moment to bind, then report.
sleep 1
if systemctl is-active --quiet "$SERVICE"; then
  echo "✓ frontend rebuilt + $SERVICE restarted (build $(cat "$ROOT/frontend/.next/BUILD_ID" 2>/dev/null || echo '?'))"
else
  echo "✗ $SERVICE is NOT active after restart — check: journalctl -u $SERVICE -n 50" >&2
  exit 1
fi
