#!/usr/bin/env bash
# Nightly backup of the trading ledger (the source of truth) + the config files
# a restore actually needs. Uses sqlite3 .backup (safe online copy, WAL-aware),
# verifies every copy with PRAGMA integrity_check, and fails LOUDLY (non-zero
# exit + [BACKUP FAILED] in the log) so a watchdog can page on it.
set -euo pipefail

DB=/home/mbelli/dev/help_me_trade/data/trading.db
DATA=/home/mbelli/dev/help_me_trade/data
DEST=/home/mbelli/backups
STAMP=$(date +%F)

fail() {
  echo "$(date -Is) [BACKUP FAILED] $*" >&2
  exit 1
}

mkdir -p "$DEST"
/usr/bin/sqlite3 "$DB" ".backup '$DEST/trading-$STAMP.db'" \
  || fail "sqlite3 .backup errored"

# A backup that doesn't restore is hope, not a backup — verify the copy.
CHECK=$(/usr/bin/sqlite3 "$DEST/trading-$STAMP.db" "PRAGMA integrity_check;" 2>&1) \
  || fail "integrity_check errored on trading-$STAMP.db: $CHECK"
[[ "$CHECK" == "ok" ]] || fail "integrity_check NOT ok on trading-$STAMP.db: $CHECK"

# Config a restore needs beside the ledger: app settings (strategy arming!),
# Schwab account labels, and the original broker export JSONs.
tar -czf "$DEST/hmt-config-$STAMP.tar.gz" \
  -C "$DATA" settings.json schwab_account_labels.json imports \
  || fail "config tarball errored"

# Retain the last 14 daily snapshots (DB + config).
find "$DEST" -maxdepth 1 -name 'trading-*.db' -mtime +14 -delete
find "$DEST" -maxdepth 1 -name 'hmt-config-*.tar.gz' -mtime +14 -delete

# --- OFF-BOX COPY ---------------------------------------------------------------
# RETIRED 2026-08-20: mattpc used to PULL this directory nightly at 03:30 over
# Tailscale (Task Scheduler -> deploy/pull-backups.ps1). Dual-booting that box into
# Omarchy for three days killed the pull silently — last_pull froze at 2026-08-18
# and nothing noticed, because a job that pages only on its own failure cannot
# report its own absence. A backup that depends on the other machine being awake,
# and on which OS it booted, is not a backup.
# The off-box copy is now a PUSH from serv, which is always on:
#   tools/ledger_core_backup.sh  (hmt-offsite-backup.timer, 03:15 UTC)
# It reads the snapshot this script writes, so THIS job staying healthy is its
# precondition — hence the 30h freshness guard on the source at the top of it.
# ----------------------------------------------------------------------------------

echo "$(date -Is) backup OK -> $DEST/trading-$STAMP.db (integrity ok) + hmt-config-$STAMP.tar.gz"
