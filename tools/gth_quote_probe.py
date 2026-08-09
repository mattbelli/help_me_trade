"""Read-only probe: during the Cboe GTH overnight session, does Alpaca quote?

The question that gates the whole overnight-aggressor lane: for index contracts
that ARE printing overnight, is there a usable two-sided quote at print time?

Method — one chain-snapshot call per root gives latestTrade AND latestQuote per
contract, so "printing now" and "quoted now" are measured on the same object.
A contract counts as CLASSIFIABLE if its latestTrade is recent and its
latestQuote is (a) two-sided and (b) stamped at/after that trade.

No sockets, no DB writes, no collector changes. Safe alongside the live stack.
Alpaca has NO historical options-quotes endpoint (measured 2026-07-31, 404), so
a live probe inside the session is the only way to answer this.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.factory import get_broker  # noqa: E402

ET = zoneinfo.ZoneInfo("America/New_York")
OUT_DIR = str(Path(__file__).resolve().parent.parent / "data/research/gth_quote_probe")

ROOTS = ["SPXW", "XSP"]
# strike bands bracket spot (SPX ~7500, XSP ~750) generously enough to survive a
# weekend gap without re-centering mid-run
BANDS = {"SPXW": (7000.0, 8000.0), "XSP": (700.0, 800.0)}
INTERVAL_SEC = 300
TRADE_FRESH_SEC = 900  # "printing now" window


def _ts(raw: object) -> dt.datetime | None:
    if not raw:
        return None
    s = str(raw).replace("Z", "+00:00")
    # Alpaca returns nanoseconds; python parses at most microseconds
    if "." in s:
        head, _, tail = s.partition(".")
        digits = "".join(c for c in tail if c.isdigit())[:6]
        offset = tail[len(digits):].lstrip("0123456789")
        s = f"{head}.{digits:<06s}{offset}"
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def sample(broker, now: dt.datetime) -> dict:
    row: dict = {"poll_et": now.isoformat(), "roots": {}}
    for root in ROOTS:
        lo, hi = BANDS[root]
        try:
            snaps = broker.get_option_chain_snapshot(
                root,
                strike_price_gte=lo,
                strike_price_lte=hi,
                limit=1000,
                max_pages=3,
            )
        except Exception as exc:
            row["roots"][root] = {"error": str(exc)[:200]}
            continue

        n_contracts = len(snaps)
        printing = quoted = classifiable = 0
        spreads: list[float] = []
        newest_q = newest_t = None
        for _occ, snap in snaps.items():
            if not isinstance(snap, dict):
                continue
            lt = snap.get("latestTrade") or {}
            lq = snap.get("latestQuote") or {}
            t_ts, q_ts = _ts(lt.get("t")), _ts(lq.get("t"))
            if t_ts and (newest_t is None or t_ts > newest_t):
                newest_t = t_ts
            if q_ts and (newest_q is None or q_ts > newest_q):
                newest_q = q_ts

            fresh_trade = bool(
                t_ts and (now - t_ts).total_seconds() <= TRADE_FRESH_SEC
            )
            bid, ask = lq.get("bp"), lq.get("ap")
            two_sided = bool(bid and ask and float(ask) > float(bid) > 0)
            if fresh_trade:
                printing += 1
            if two_sided:
                quoted += 1
            # the real metric: a fresh print whose quote is not stale relative to it
            if fresh_trade and two_sided and q_ts and t_ts and q_ts >= t_ts:
                classifiable += 1
                mid = (float(bid) + float(ask)) / 2.0
                if mid > 0:
                    spreads.append((float(ask) - float(bid)) / mid)

        spreads.sort()
        row["roots"][root] = {
            "contracts": n_contracts,
            "printing": printing,
            "two_sided": quoted,
            "classifiable": classifiable,
            "pct_classifiable": round(100.0 * classifiable / printing, 1)
            if printing
            else None,
            "median_rel_spread": round(spreads[len(spreads) // 2], 4)
            if spreads
            else None,
            "newest_trade": newest_t.isoformat() if newest_t else None,
            "newest_quote": newest_q.isoformat() if newest_q else None,
        }
    return row


def main() -> None:
    once = "--once" in sys.argv
    stop_h, stop_m = 9, 20  # just past the 09:15 ET GTH close
    broker = get_broker("alpaca")
    os.makedirs(OUT_DIR, exist_ok=True)
    out = f"{OUT_DIR}/{dt.datetime.now(ET):%Y-%m-%d}.jsonl"
    print(f"gth probe start {dt.datetime.now(ET):%a %H:%M:%S} ET (once={once}) -> {out}")
    with open(out, "a") as fh:
        while True:
            now = dt.datetime.now(ET)
            row = sample(broker, now)
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            for root, r in row["roots"].items():
                if "error" in r:
                    print(f"{now:%H:%M:%S} {root}: ERROR {r['error']}")
                else:
                    print(
                        f"{now:%H:%M:%S} {root}: contracts={r['contracts']:>4} "
                        f"printing={r['printing']:>4} two_sided={r['two_sided']:>4} "
                        f"classifiable={r['classifiable']:>4} "
                        f"({r['pct_classifiable']}%)  newest_quote={r['newest_quote']}"
                    )
            if once:
                return
            t = dt.datetime.now(ET)
            if t.weekday() < 5 and (t.hour, t.minute) >= (stop_h, stop_m) and t.hour < 20:
                print("past GTH close; stopping")
                return
            time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
