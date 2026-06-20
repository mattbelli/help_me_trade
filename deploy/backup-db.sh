#!/usr/bin/env bash
# Nightly SQLite backup of the trading ledger (the source of truth).
# Uses sqlite3 .backup (safe online copy, WAL-aware). Installed via crontab.
set -euo pipefail

DB=/home/mbelli/dev/help_me_trade/data/trading.db
DEST=/home/mbelli/backups
STAMP=$(date +%F)

mkdir -p "$DEST"
/usr/bin/sqlite3 "$DB" ".backup '$DEST/trading-$STAMP.db'"

# Retain the last 14 daily snapshots.
find "$DEST" -maxdepth 1 -name 'trading-*.db' -mtime +14 -delete

# --- OFF-BOX COPY (fill in a Tailscale destination for real DR) ---------------
# A backup on the same disk is not disaster recovery. Uncomment and set a target
# reachable over the tailnet (e.g. the old Windows box or a NAS):
# rsync -a "$DEST/trading-$STAMP.db" mbelli@<tailnet-host>:/path/to/hmt-backups/
# ------------------------------------------------------------------------------

echo "$(date -Is) backup OK -> $DEST/trading-$STAMP.db"
