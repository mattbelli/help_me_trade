#!/usr/bin/env bash
# One-shot post-close swap: stop the sequential OI backfill, relaunch as 3
# disjoint-range shards (skip-existing makes boundaries safe). Scheduled for
# 20:20 UTC 2026-07-28 — after the 16:15 ET index settlement window, when the
# live OPRA socket no longer competes for the downlink.
set -u
ROOT=/home/mbelli/dev/help_me_trade
LOG=$ROOT/data/opra_oi_backfill.log
echo "$(date -u +%FT%TZ) shard swap: stopping sequential unit" >> "$LOG"
systemctl --user stop opra-oi-backfill 2>/dev/null
sleep 5
i=0
for range in "2025-07-28 2025-12-01" "2025-12-01 2026-04-01" "2026-04-01 2026-07-25"; do
  i=$((i+1))
  set -- $range
  systemd-run --user --unit="opra-oi-shard$i" -p MemoryMax=12G -p Nice=10 \
    -p IOSchedulingClass=idle -p WorkingDirectory="$ROOT" \
    -p StandardOutput=append:"$LOG" -p StandardError=append:"$LOG" \
    "$ROOT/.venv/bin/python" tools/opra_oi_backfill.py --start "$1" --end "$2"
done
echo "$(date -u +%FT%TZ) shard swap: 3 shards launched" >> "$LOG"
