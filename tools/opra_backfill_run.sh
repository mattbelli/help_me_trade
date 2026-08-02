#!/usr/bin/env bash
# Sequential defs-then-tcbbo backfill. Sequential, not parallel: two concurrent
# ALL_SYMBOLS pulls doubled IO on a box that had already swapped itself flat.
set -u
cd /home/mbelli/dev/help_me_trade
S=2025-07-28
E=2026-07-25
echo "=== RUN START $(date -Is) ==="
for schema in definition tcbbo; do
  echo "--- $schema $(date -Is) ---"
  nice -n 10 ionice -c2 -n6 .venv/bin/python tools/opra_tcbbo_backfill.py \
      --schema "$schema" --start "$S" --end "$E"
  echo "--- $schema exit=$? $(date -Is) ---"
done
echo "=== RUN END $(date -Is) ==="
