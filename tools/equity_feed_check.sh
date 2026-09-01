#!/usr/bin/env bash
# Is the equity tape actually receiving? -> "ok <detail>" | "fail <detail>" on stdout.
#
# Written 2026-09-01 after the equity SIP socket went silent at 09:36:52 UTC and
# stayed silent for 6.5h — through the open — with the process healthy, the TCP
# socket ESTAB and reconnects=0. Nothing on the box noticed. The collector now
# detects that itself (collectors/equity_rs/src/main.rs, ping/pong watchdog) and
# publishes it; this reads what it publishes so the failure reaches a phone.
#
# Reads the collector's own 1s snapshot rather than probing Alpaca: the snapshot
# is the collector's view of its own upstream, which is the thing that was lying.
#
# Deliberately NOT keyed on message rate — a quiet overnight tape is healthy and
# would false-alarm every night. `connected` is the collector's watchdog verdict,
# and it stays true through an honestly quiet market.
set -uo pipefail

SNAP="${COLLECTOR_STATS_DIR:-${HOME}/dev/help_me_trade/data/collector_stats}/equity.json"
MAX_AGE_S=120        # snapshot is rewritten every 1s; 120s stale = the writer is gone

[ -f "$SNAP" ] || { echo "fail snapshot missing ($SNAP) — collector not running?"; exit 0; }

read -r ts connected stalls reconnects rate60 < <(
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("ts", 0), d.get("connected"), d.get("stalls", 0),
      d.get("reconnects", 0), d.get("rate_60s", 0))' "$SNAP" 2>/dev/null
) || { echo "fail snapshot unreadable ($SNAP)"; exit 0; }

age=$(python3 -c "import time,sys; print(int(time.time()-float(sys.argv[1])))" "$ts" 2>/dev/null || echo 999)

if [ "$age" -gt "$MAX_AGE_S" ]; then
    echo "fail snapshot ${age}s stale — the collector is wedged or dead"
elif [ "$connected" != "True" ]; then
    echo "fail upstream DOWN (stalls=${stalls} reconnects=${reconnects}) — feed disconnected, tape is gapping"
else
    echo "ok upstream live (rate60=${rate60}/s stalls=${stalls} reconnects=${reconnects} age=${age}s)"
fi
