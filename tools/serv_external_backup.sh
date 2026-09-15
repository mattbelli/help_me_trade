#!/usr/bin/env bash
# Nightly backup of irreplaceable serv data to the external USB drive (/mnt/backup).
# Design (2026-07-30): the drive is NTFS and shares space with a media library, so
# everything lives under /mnt/backup/hmt-backups/. Live SQLite DBs are copied with
# the online .backup API (consistent under writers); everything else in data/ is
# rsync'd WITHOUT --delete so a bad rm on serv can never propagate to the backup.
# DBs are kept as dated versioned copies (not a mirror) so one corrupt night can't
# overwrite the only good copy. Cadence is chosen by VALUE, not size (2026-08-20):
# everything goes nightly except the re-downloadable bulk corpora in WEEKLY_DBS.
# Retention is grandfather-father-son -- newest DAILY_KEEP copies plus the newest
# SUNDAY_KEEP Sunday-dated ones -- so "yesterday" and "five weeks ago" both survive
# without holding five weeks of 5 GB dailies. The optional raw equity tapes are
# different: they are replay fixtures, not a permanent archive, and are capped on
# both serv and USB.
# A source DB older than its newest backup is skipped (static legacy copies get one
# copy ever). Failure pings the ntfy dead-man topic; success stamps last_success.
set -uo pipefail

SRC="/home/mbelli/dev/help_me_trade"
DST="/mnt/backup/hmt-backups"
NTFY_TOPIC="${NTFY_TOPIC:-hmt-deadman-0033ce1c5edb753f9932}"
# Re-downloadable research corpora: huge, and a vendor can hand them back. Deferred
# to Sundays purely to save drive space -- never the ledger.
WEEKLY_DBS="flow_corpus oi_walls"
DAILY_KEEP=7
SUNDAY_KEEP=5
WEEKLY_KEEP=2
RAW_TAPE_KEEP=5
RAW_TAPE_REFERENCE="2026-08-18.mpk.zst"
RAW_TAPE_MIN_BYTES=$((1024 * 1024 * 1024))
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

# Grandfather-father-son prune. A dated copy survives if it is among the newest
# $2 copies OR among the newest $3 Sunday-dated ones -- so the last week is dense
# and Sundays carry the reach back a month, without paying for a month of dailies.
prune_versions() {
  local pat="$1" daily="$2" sundays="$3" all keepers f d
  all="$(ls -1 $pat 2>/dev/null | sort)"
  [ -n "$all" ] || return 0
  keepers="$(printf '%s\n' "$all" | tail -n "$daily")"
  keepers="$keepers
$(printf '%s\n' "$all" | while read -r f; do
    d="$(basename "$f" | grep -o '[0-9]\{8\}' | tail -1)"
    [ "$(date -d "$d" +%u 2>/dev/null)" = "7" ] && printf '%s\n' "$f"
  done | tail -n "$sundays")"
  keepers="$(printf '%s\n' "$keepers" | grep -v '^$')"
  # Never run the delete with an empty keep-set: grep -v with no patterns matches
  # every line, which would wipe every copy we have.
  [ -n "$keepers" ] || return 0
  printf '%s\n' "$all" | grep -vxF -f <(printf '%s\n' "$keepers") | xargs -r rm -f
}

# Keep the five latest complete raw tapes plus the Rust/Python parity reference.
# Today's ET file is actively written while this job can run, so never touch it.
# The same policy applies at the source and destination: rsync's deliberate
# no-delete rule protects against accidental source loss, not intended retention.
prune_raw_tapes() {
  local dir="$1" today_et file name keep_name found
  local -a complete=() keep=()

  [ -d "$dir" ] || return 0
  today_et="$(TZ=America/New_York date +%F)"
  mapfile -t complete < <(
    find "$dir" -maxdepth 1 -type f -name '????-??-??.mpk.zst' \
      -size +"${RAW_TAPE_MIN_BYTES}"c -printf '%f\n' |
      awk -v today="$today_et" '$0 < today {print}' | sort
  )
  if [ "${#complete[@]}" -gt 0 ]; then
    mapfile -t keep < <(printf '%s\n' "${complete[@]}" | tail -n "$RAW_TAPE_KEEP")
  fi
  keep+=("$RAW_TAPE_REFERENCE")

  for file in "$dir"/*.mpk.zst; do
    [ -e "$file" ] || continue
    name="${file##*/}"
    [ "$name" = "$today_et.mpk.zst" ] && continue
    found=false
    for keep_name in "${keep[@]}"; do
      [ "$name" = "$keep_name" ] && { found=true; break; }
    done
    if ! $found; then
      echo "prune raw tape: $file"
      rm -f -- "$file" || { echo "ERROR: raw-tape prune failed for $file"; errors=$((errors + 1)); }
    fi
  done
}

# --- 1. SQLite databases: online .backup -> dated copies, tiered rotation ----------
for db in "$SRC"/data/*.db; do
  name="$(basename "$db" .db)"
  size=$(stat -c%s "$db")
  # The rule this replaces deferred anything >3 GB to Sundays with a bare
  # `continue` that logged NOTHING. When trading.db crossed 3 GB on 2026-08-05 the
  # ledger silently dropped to weekly and the log never said a word -- the drive
  # looked healthy while the one irreplaceable file went six nights out of seven
  # without a copy. Size is not value: trading.db goes nightly at any size, the
  # bulk corpora defer, and a deferral now says so out loud.
  weekly=false
  case " $WEEKLY_DBS " in *" $name "*) weekly=true ;; esac
  if $weekly; then
    if [ "$(date -u +%u)" != "7" ]; then
      echo "defer $name ($((size / 1024 / 1024 / 1024)) GB, weekly lane -- Sundays only)"
      continue
    fi
    daily_keep=$WEEKLY_KEEP; sunday_keep=$WEEKLY_KEEP
  else
    daily_keep=$DAILY_KEEP; sunday_keep=$SUNDAY_KEEP
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
    prune_versions "$DST/db/$name.[0-9]*.db" "$daily_keep" "$sunday_keep"
  else
    rm -f "$out.part"; echo "ERROR: sqlite backup failed for $name"; errors=$((errors + 1))
  fi
done

# --- 2. Data mirror, then explicit raw-tape retention -------------------------------
if ! rsync -a --no-perms --no-owner --no-group --omit-dir-times \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' --exclude '*.part' \
  "$SRC/data/" "$DST/data/"; then
  echo "ERROR: data rsync failed"
  errors=$((errors + 1))
else
  prune_raw_tapes "$SRC/data/equity_tape_raw"
  prune_raw_tapes "$DST/data/equity_tape_raw"
fi

# --- 3. Config + secrets + agent memory: dated tarball, keep 30 --------------------
tarball="$DST/config/config.$TODAY.tar.gz"
crontab -l > /tmp/crontab.dump 2>/dev/null || true
# agent memories only, not the (large) session transcripts beside them
mem_dirs=$(cd "$HOME" && ls -d .claude/projects/*/memory 2>/dev/null)
tar -czf "$tarball.part" \
  -C / etc/fstab \
  -C "$SRC" .env \
  -C /tmp crontab.dump \
  -C "$HOME" .config/systemd/user .config/rclone $mem_dirs 2>/dev/null
if [ -s "$tarball.part" ]; then
  mv "$tarball.part" "$tarball"
  ls -1 "$DST/config"/config.*.tar.gz 2>/dev/null | sort | head -n -30 | xargs -r rm -f
else
  rm -f "$tarball.part"; echo "ERROR: config tarball empty"; errors=$((errors + 1))
fi
rm -f /tmp/crontab.dump

# --- 4. Cross-job canary: is the OFF-SITE push still alive? -------------------------
# The mattpc pull died on 2026-08-18 simply by not running, and nothing noticed for
# two days: a job that pages only on its own failure cannot report its own absence.
# So each nightly job now watches the other's success stamp. This one is loud about
# the off-site push; tools/ledger_core_backup.sh is loud about this one.
offsite_stamp="/home/mbelli/backups/last_offsite"
if [ -f "$offsite_stamp" ]; then
  age_h=$(( ( $(date -u +%s) - $(date -u -r "$offsite_stamp" +%s) ) / 3600 ))
  if [ "$age_h" -gt 36 ]; then
    echo "WARN: off-site ledger push is ${age_h}h stale"
    curl -fsS -m 15 -H "Title: off-site ledger push is ${age_h}h stale" -H "Priority: high" \
      -d "last_offsite has not moved in ${age_h}h — the only copy that leaves serv is dead" \
      "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
  fi
else
  echo "WARN: $offsite_stamp missing — off-site push has never succeeded"
fi

# --- 5. Verdict --------------------------------------------------------------------
[ "$errors" -eq 0 ] || fail "$errors step(s) failed — see data/external_backup.log on serv"
date -u +%Y-%m-%dT%H:%M:%SZ > "$DST/last_success"
echo "backup OK $(date -u +%FT%TZ) — drive: $(df -h /mnt/backup | awk 'NR==2{print $4" free"}')"
