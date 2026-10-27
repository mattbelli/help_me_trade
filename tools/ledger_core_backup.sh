#!/usr/bin/env bash
# Nightly OFF-SITE backup of the irreplaceable core of trading.db (2026-08-20).
#
# Why this exists: until today the only copy that left serv was a PULL run by
# mattpc's Task Scheduler. Booting that box into Omarchy for three days killed
# the pull silently (last_pull went stale 2026-08-18) — a backup that depends on
# the OTHER machine being awake and on a particular OS is not a backup. This job
# runs on serv, which is always on, and PUSHES.
#
# Why a "core" and not the whole DB: trading.db is 5.4 GB, but ~3 GB of that is
# re-downloadable market data (tape events, fusion score history, options flow,
# bars, news). Losing that costs a re-download. The ~1.2 GB that remains — the
# ledger, the executions, the decisions, the calibration outcomes, the agent's
# memories — cannot be reconstructed from any vendor at any price. Shipping only
# that makes a nightly off-site push small enough to actually happen (~185 MB gz).
#
# Source is the 03:00 static snapshot (deploy/backup-db.sh), NOT the live DB:
# it is already PRAGMA integrity_check'd and nothing is writing to it.
# Encryption is rclone's crypt remote — content AND filenames are encrypted on
# serv before a byte leaves the box, so Google stores opaque blobs.
set -uo pipefail

SRC_DIR="/home/mbelli/backups"
DATA="/home/mbelli/dev/help_me_trade/data"
STAGE="$SRC_DIR/offsite"
REMOTE="${HMT_OFFSITE_REMOTE:-gdrive-crypt:ledger-core}"
RCLONE="$HOME/.local/bin/rclone"
NTFY_TOPIC="${NTFY_TOPIC:-hmt-deadman-0033ce1c5edb753f9932}"
KEEP_DAYS=30
STAMP="$(date -u +%Y-%m-%d)"

fail() {
  echo "$(date -Is) [OFFSITE FAILED] $1" >&2
  curl -fsS -m 15 -H "Title: off-site ledger backup FAILED" -H "Priority: high" \
    -d "$1" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
  rm -rf "$STAGE"
  exit 1
}

# Bulk tables EXCLUDED from the core. This is an exclude-list on purpose: a new
# table is backed up by default, so a new ledger table can never be silently
# missed. The cost of the inverse mistake — a new bulk table quietly bloating the
# push — is a big file, which is loud. Silent data loss is not.
EXCLUDE="equity_tape_events fusion_score_history options_flow_events options_flow_daily
snapshot_analytics flow_rarity_cells flow_rarity_names flow_rarity_meta momentum_daily_bars
daily_bars news_items news_symbols social_mentions social_posts social_post_symbols
earnings_calendar earnings_coverage index_pressure_minutes index_pressure_days
fusion_spot_cache fusion_atr_cache fusion_day_volume fusion_z_baseline fusion_session_levels
poll_runs flow_hot_lane regime_macro filings_events filings_seen session_node_events
bus_cursors bus_ingest_sequences lane_stats
memories_fts memories_fts_config memories_fts_data memories_fts_docsize memories_fts_idx"

command -v "$RCLONE" >/dev/null || fail "rclone missing at $RCLONE"
"$RCLONE" listremotes 2>/dev/null | grep -q "^${REMOTE%%:*}:" \
  || fail "rclone remote '${REMOTE%%:*}' is not configured — run: rclone config"

# --- 1. Pick the verified static snapshot (never the live DB) ----------------------
SNAP="$(find "$SRC_DIR/" -maxdepth 1 -name 'trading-*.db' -mmin -1800 2>/dev/null | sort | tail -1)"
[ -n "$SNAP" ] || fail "no trading-*.db snapshot under 30h old in $SRC_DIR — the 03:00 backup is dead"

rm -rf "$STAGE"; mkdir -p "$STAGE" || fail "cannot create $STAGE"

# --- 2. Dump every table except the re-downloadable bulk ---------------------------
tables="$(sqlite3 "$SNAP" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")"
[ -n "$tables" ] || fail "no tables found in $(basename "$SNAP")"
keep=""
for t in $tables; do
  case " $(echo $EXCLUDE) " in *" $t "*) continue ;; esac
  keep="$keep $t"
done
[ -n "$keep" ] || fail "exclude list swallowed every table — refusing to ship an empty core"

{
  echo "PRAGMA foreign_keys=OFF;"
  echo "BEGIN TRANSACTION;"
  for t in $keep; do
    sqlite3 "$SNAP" ".dump $t" | grep -v -E '^(PRAGMA foreign_keys|BEGIN TRANSACTION|COMMIT);$'
  done
  echo "COMMIT;"
} | gzip -6 > "$STAGE/trading-core.sql.gz" || fail "core dump failed"
[ -s "$STAGE/trading-core.sql.gz" ] || fail "core dump is empty"

# --- 3. Prove it restores. A backup that doesn't restore is hope, not a backup. ----
gunzip -c "$STAGE/trading-core.sql.gz" | sqlite3 "$STAGE/verify.db" \
  || fail "core dump does not replay into a fresh DB"
chk="$(sqlite3 "$STAGE/verify.db" "PRAGMA integrity_check;" 2>&1)"
[ "$chk" = "ok" ] || fail "restored core failed integrity_check: $chk"
src_rows="$(sqlite3 "$SNAP" "SELECT COUNT(*) FROM order_executions;")"
dst_rows="$(sqlite3 "$STAGE/verify.db" "SELECT COUNT(*) FROM order_executions;")"
[ "$src_rows" = "$dst_rows" ] \
  || fail "order_executions row count mismatch: snapshot=$src_rows restored=$dst_rows"
rm -f "$STAGE/verify.db"

# --- 4. The config a restore needs beside the ledger, + the agent's memories -------
mem_dirs=$(cd "$HOME" && ls -d .claude/projects/*/memory 2>/dev/null)
tar -czf "$STAGE/config.tar.gz" \
  -C "$DATA" settings.json schwab_account_labels.json \
  -C "$HOME" $mem_dirs 2>/dev/null
[ -s "$STAGE/config.tar.gz" ] || fail "config tarball is empty"

# --- 5. Push (rclone crypt encrypts contents + filenames before upload) ------------
out="$SRC_DIR/ledger-core-$STAMP.tar"
tar -cf "$out" -C "$STAGE" trading-core.sql.gz config.tar.gz || fail "packaging failed"
"$RCLONE" copyto "$out" "$REMOTE/ledger-core-$STAMP.tar" \
  --transfers 1 --retries 3 --low-level-retries 10 --timeout 5m \
  || fail "rclone push to $REMOTE failed"

# Confirm the remote actually has it at the right size — rclone can exit 0 on a
# no-op if the destination path is wrong.
local_sz=$(stat -c%s "$out")
remote_sz=$("$RCLONE" size "$REMOTE/ledger-core-$STAMP.tar" --json 2>/dev/null | grep -o '"bytes":[0-9]*' | cut -d: -f2)
[ "$local_sz" = "$remote_sz" ] \
  || fail "off-site size mismatch: local=$local_sz remote=${remote_sz:-absent}"

"$RCLONE" delete "$REMOTE" --min-age "${KEEP_DAYS}d" 2>/dev/null || true

# Cross-job canary, the mirror of the one in serv_external_backup.sh: a nightly job
# cannot report its own absence, so the two jobs watch each other's success stamps.
usb_stamp="/mnt/backup/hmt-backups/last_success"
if [ -f "$usb_stamp" ]; then
  age_h=$(( ( $(date -u +%s) - $(date -u -r "$usb_stamp" +%s) ) / 3600 ))
  if [ "$age_h" -gt 36 ]; then
    curl -fsS -m 15 -H "Title: USB backup is ${age_h}h stale" -H "Priority: high" \
      -d "/mnt/backup last_success has not moved in ${age_h}h" \
      "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
  fi
fi

rm -rf "$STAGE" "$out"
date -u +%Y-%m-%dT%H:%M:%SZ > "$SRC_DIR/last_offsite"
echo "$(date -Is) offsite OK -> $REMOTE/ledger-core-$STAMP.tar ($((local_sz / 1024 / 1024)) MB, $(echo $keep | wc -w) tables)"
