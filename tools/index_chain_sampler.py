"""Index 0-1DTE chain concentration sampler — Phase A of the pool-sizing study.

Founder hypothesis (2026-07-16): a few strikes (ATM, ~3%, ~5% OTM) carry enough of
the 0-1DTE index premium that a small subscription pool reproduces the full-chain
picture. This tool MEASURES the volume-concentration half of that with zero
production risk: read-only REST chain snapshots for the gauge universe's 0-1DTE
contracts (+ underlying spot for moneyness), sampled every minute through the two
gauge windows (09:25-10:05 and 15:25-16:05 ET), appended as JSONL. Analysis
computes per-minute volume deltas offline and answers: what fraction of 0DTE
premium do the top-N strikes / a moneyness ladder capture, minute by minute?

What this deliberately does NOT answer: signed-pressure fidelity (needs the full
classified tape — Phase B). Don't conclude direction from volume alone.

Run: `uv run python tools/index_chain_sampler.py` (foreground; sleeps between
windows, exits after the close window). One JSONL file per day under
data/research/chain_samples/ (gitignored with the rest of data/).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.factory import get_broker  # noqa: E402
from imports.option_symbol import parse_occ_symbol  # noqa: E402

_ET = ZoneInfo("America/New_York")

# Indexes by fetch root (SPXW rides SPX) + a megacap slice for the founder's
# "carry this over to large caps" extension. DTE≤1 only — the study's subject.
SAMPLE_UNDERLYINGS = ("SPY", "QQQ", "IWM", "DIA", "SPX", "TSLA", "NVDA", "AAPL")
MAX_DTE = 1
SAMPLE_SECONDS = 60
# 5-min shoulders around the gauge windows so deltas exist at the edges.
WINDOWS_ET = ((9 * 60 + 25, 10 * 60 + 5), (15 * 60 + 25, 16 * 60 + 5))
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "research" / "chain_samples"


def _now_min(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _in_window(now: datetime) -> bool:
    m = _now_min(now)
    return any(start <= m <= end for start, end in WINDOWS_ET)


def _next_window_start(now: datetime) -> int | None:
    m = _now_min(now)
    starts = [start for start, _ in WINDOWS_ET if start > m]
    return min(starts) if starts else None


def _sample_once(broker, today_et: str, out) -> int:
    ts = datetime.now(_ET).isoformat(timespec="seconds")
    rows = 0
    for u in SAMPLE_UNDERLYINGS:
        spot = None
        try:
            snap = broker.get_stock_snapshot_fields(u)
            if snap and snap.get("last_price") is not None:
                spot = float(snap["last_price"])
        except Exception:
            pass  # index roots (SPX) have no stock snapshot — moneyness from strike/spot of SPY proxy is NOT used; strike is stored raw
        try:
            chain = broker.get_option_chain_snapshot(u, limit=250, max_pages=8)
        except Exception as exc:
            out.write(json.dumps({"ts": ts, "u": u, "error": str(exc)[:200]}) + "\n")
            continue
        if not isinstance(chain, dict):
            continue
        for occ, csnap in chain.items():
            if not isinstance(csnap, dict):
                continue
            parsed = parse_occ_symbol(str(occ).strip().upper())
            if parsed is None:
                continue
            dte = (parsed.expiration - datetime.now(_ET).date()).days
            if dte > MAX_DTE or dte < 0:
                continue
            trade = csnap.get("latestTrade") or {}
            day = csnap.get("dailyBar") or {}
            out.write(
                json.dumps(
                    {
                        "ts": ts,
                        "u": u,
                        "occ": str(occ).strip().upper(),
                        "underlying_parsed": parsed.underlying,
                        "right": parsed.right,
                        "strike": float(parsed.strike),
                        "dte": dte,
                        "spot": spot,
                        "day_vol": (day.get("v") if isinstance(day, dict) else None),
                        "last_price": (trade.get("p") if isinstance(trade, dict) else None),
                        "last_ts": (trade.get("t") if isinstance(trade, dict) else None),
                    }
                )
                + "\n"
            )
            rows += 1
    out.flush()
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    broker = get_broker("alpaca")
    print(f"index_chain_sampler: sampling {SAMPLE_UNDERLYINGS} DTE<=1 every "
          f"{SAMPLE_SECONDS}s in ET windows {WINDOWS_ET}", flush=True)
    while True:
        now = datetime.now(_ET)
        today_et = now.date().isoformat()
        if _in_window(now):
            path = OUT_DIR / f"{today_et}.jsonl"
            with path.open("a") as out:
                n = _sample_once(broker, today_et, out)
            print(f"{now.isoformat(timespec='seconds')} sampled {n} rows", flush=True)
            time.sleep(SAMPLE_SECONDS)
            continue
        nxt = _next_window_start(now)
        if nxt is None:
            print("index_chain_sampler: past the close window — done for the day",
                  flush=True)
            return
        wait_s = (nxt - _now_min(now)) * 60 - now.second
        print(f"index_chain_sampler: sleeping {wait_s}s until next window", flush=True)
        time.sleep(max(wait_s, 30))


if __name__ == "__main__":
    main()
