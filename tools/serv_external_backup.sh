#!/usr/bin/env bash
# Nightly backup of irreplaceable serv data to the external USB drive (/mnt/backup).
# Design (2026-07-30): the drive is NTFS and shares space with a media library, so
# everything lives under /mnt/backup/hmt-backups/. Live SQLite DBs are copied with
# the online .backup API (consistent under writers); everything else in data/ is
# rsync'd WITHOUT --delete so a bad rm on serv can never propagate to the backup.
# DBs are kept as dated versioned copies (not a mirror) so one corrupt night can't
# overwrite the only good copy: >3 GB DBs weekly (Sun) keep 4, smaller nightly keep 14.
# A source DB older than its newest backup is skipped (static legacy copies get one
# copy ever). Failure pings the ntfy dead-man topic; success stamps last_success.
set -uo pipefail

SRC="/home/mbelli/dev/help_me_trade"
DST="/mnt/backup/hmt-backups"
NTFY_TOPIC="${NTFY_TOPIC:-hmt-deadman-0033ce1c5edb753f9932}"
BIG_DB_BYTES=$((3 * 1024 * 1024 * 1024))
TODAY="$(date -u +%Y%m%d)"
LOG_DIR="$DST/logs"

fail() {
  echo "FATAL: $1"
  curl -fsS -m 10 -H "Title: serv external backup FAILED" -d "$1" \
    "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
  exit 1
}

# The one guard that matters: never write to an unmounted mountpoint (fills the M.2).
mountpoint -q /mnt/backup || fail "/mnt/backup is not mounted"
mkdir -p "$DST/db" "$DST/data" "$DST/config" "$LOG_DIR" || fail "cannot create $DST tree"

errors=0

# --- 1. SQLite databases: online .backup -> dated copies, tiered rotation ----------
for db in "$SRC"/data/*.db; do
  name="$(basename "$db" .db)"
  size=$(stat -c%s "$db")
  if [ "$size" -gt "$BIG_DB_BYTES" ]; then
    [ "$(date -u +%u)" = "7" ] || continue   # big DBs: Sundays only
    keep=4
  else
    keep=14
  fi
  newest="$(ls -1 "$DST/db/$name".[0-9]*.db 2>/dev/null | sort | tail -1)"
  if [ -n "$newest" ] && [ "$db" -ot "$newest" ]; then
    echo "skip $name (unchanged since $(basename "$newest"))"
    continue
  fi
  out="$DST/db/$name.$TODAY.db"
  echo "sqlite backup: $name ($((size / 1024 / 1024)) MB)"
  # trading.db is written constantly: an online .backup of it restarts on every
  # WAL checkpoint and can spin forever against this slow drive. Prefer the
  # static, already-integrity-checked 03:00 snapshot (deploy/backup-db.sh)
  # when one is fresh (<48h); fall back to a live .backup if it isn't.
  snap=""
  if [ "$name" = "trading" ]; then
    snap="$(find /home/mbelli/backups -maxdepth 1 -name 'trading-*.db' \
            -mmin -2880 2>/dev/null | sort | tail -1)"
  fi
  ok=true
  if [ -n "$snap" ]; then
    echo "  (copying static snapshot $(basename "$snap"))"
    cp "$snap" "$out.part" || ok=false
  else
    sqlite3 "$db" ".backup '$out.part'" || ok=false
  fi
  if $ok && mv "$out.part" "$out"; then
    ls -1 "$DST/db/$name".[0-9]*.db 2>/dev/null | sort | head -n -"$keep" | xargs -r rm -f
  else
    rm -f "$out.part"; echo "ERROR: sqlite backup failed for $name"; errors=$((errors + 1))
  fi
done

# --- 2. Everything else in data/: append-only rsync (no --delete, ever) ------------
rsync -a --no-perms --no-owner --no-group --omit-dir-times \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' --exclude '*.part' \
  "$SRC/data/" "$DST/data/" || { echo "ERROR: data rsync failed (rc=$?)"; errors=$((errors + 1)); }

# --- 3. Config + secrets + agent memory: dated tarball, keep 30 --------------------
tarball="$DST/config/config.$TODAY.tar.gz"
crontab -l > /tmp/crontab.dump 2>/dev/null || true
# agent memories only, not the (large) session transcripts beside them
mem_dirs=$(cd "$HOME" && ls -d .claude/projects/*/memory 2>/dev/null)
tar -czf "$tarball.part" \
  -C / etc/fstab \
  -C "$SRC" .env \
  -C /tmp crontab.dump \
  -C "$HOME" .config/systemd/user $mem_dirs 2>/dev/null
if [ -s "$tarball.part" ]; then
  mv "$tarball.part" "$tarball"
  ls -1 "$DST/config"/config.*.tar.gz 2>/dev/null | sort | head -n -30 | xargs -r rm -f
else
  rm -f "$tarball.part"; echo "ERROR: config tarball empty"; errors=$((errors + 1))
fi
rm -f /tmp/crontab.dump

# --- 4. Verdict --------------------------------------------------------------------
[ "$errors" -eq 0 ] || fail "$errors step(s) failed — see data/external_backup.log on serv"
date -u +%Y-%m-%dT%H:%M:%SZ > "$DST/last_success"
echo "backup OK $(date -u +%FT%TZ) — drive: $(df -h /mnt/backup | awk 'NR==2{print $4" free"}')"
