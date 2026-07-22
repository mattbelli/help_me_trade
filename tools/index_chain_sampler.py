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
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.factory import get_broker  # noqa: E402
from imports.option_symbol import parse_occ_symbol  # noqa: E402

_ET = ZoneInfo("America/New_York")

# Fetch by OCC ROOT — the snapshots endpoint's path symbol is the root, not the
# underlying (measured 2026-07-22: /snapshots/SPX serves ONLY SPX-root AM
# monthlies; the PM dailies live under /snapshots/SPXW). "SPXW rides SPX" holds
# for the OPRA websocket trade lane, NOT here. SPX stays for the OpEx-cell AM
# monthlies; SPXW is the everyday 0-1DTE index subject. + a megacap slice for
# the founder's "carry this over to large caps" extension. DTE≤1 only.
SAMPLE_UNDERLYINGS = ("SPY", "QQQ", "IWM", "DIA", "SPX", "SPXW", "TSLA", "NVDA", "AAPL")
# Roots with a listed expiry EVERY trading day — zero 0-1DTE rows from one of
# these is a collection failure, never a calendar quirk. ABSENCE MUST BE LOUD:
# the SPXW outage sat invisible for 3 sessions because files kept landing and
# only the per-root composition was wrong (07-22 postmortem).
ALWAYS_EXPECTED = ("SPY", "QQQ", "IWM", "SPXW")
MAX_DTE = 1
SAMPLE_SECONDS = 15  # quote-timeline resolution for back-pairing tests (prereg)
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


def _sample_once(broker, today_et: str, out) -> dict[str, int]:
    ts = datetime.now(_ET).isoformat(timespec="seconds")
    per_root: dict[str, int] = {}
    for u in SAMPLE_UNDERLYINGS:
        per_root[u] = 0
        spot = None
        try:
            snap = broker.get_stock_snapshot_fields(u)
            if snap and snap.get("last_price") is not None:
                spot = float(snap["last_price"])
        except Exception:
            pass  # index roots (SPX) have no stock snapshot — moneyness from strike/spot of SPY proxy is NOT used; strike is stored raw
        # Fetch per-expiry (today + tomorrow), NEVER the unfiltered chain: the
        # vendor pages alphabetically, and on the dual-root SPX chain every
        # far-dated SPX-root monthly sorts before every SPXW daily ('2' < 'W'),
        # so the page cap starved SPX of its 0-1DTE rows on non-OpEx days
        # (dark 07-20..07-22; the 07-16 smoke passed only because OpEx week put
        # the July monthly at dte<=1). Single-root chains sort nearest-first and
        # were never at risk; the expiry filter just makes them cheaper.
        chain: dict = {}
        try:
            for offset in range(MAX_DTE + 1):
                exp = (datetime.now(_ET).date() + timedelta(days=offset)).isoformat()
                page = broker.get_option_chain_snapshot(
                    u, expiration_date=exp, limit=250, max_pages=8
                )
                if isinstance(page, dict):
                    chain.update(page)
        except Exception as exc:
            out.write(json.dumps({"ts": ts, "u": u, "error": str(exc)[:200]}) + "\n")
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
            quote = csnap.get("latestQuote") or {}
            greeks = csnap.get("greeks") or {}
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
                        # prereg needs: sampled quote timeline (back-pairing at
                        # 15/30/60s) + greeks/IV (delta-adjusted residual terms)
                        "bid": (quote.get("bp") if isinstance(quote, dict) else None),
                        "ask": (quote.get("ap") if isinstance(quote, dict) else None),
                        "q_ts": (quote.get("t") if isinstance(quote, dict) else None),
                        "iv": csnap.get("impliedVolatility"),
                        "delta": (greeks.get("delta") if isinstance(greeks, dict) else None),
                        "gamma": (greeks.get("gamma") if isinstance(greeks, dict) else None),
                        "vega": (greeks.get("vega") if isinstance(greeks, dict) else None),
                    }
                )
                + "\n"
            )
            per_root[u] += 1
    for u in ALWAYS_EXPECTED:
        if per_root.get(u, 0) == 0:
            print(f"ERROR {ts} zero 0-1DTE rows for {u} — it lists dailies "
                  "every session, so this is a COLLECTION failure", flush=True)
    out.flush()
    return per_root


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    broker = get_broker("alpaca")
    day_totals: dict[str, int] = {}
    print(f"index_chain_sampler: sampling {SAMPLE_UNDERLYINGS} DTE<=1 every "
          f"{SAMPLE_SECONDS}s in ET windows {WINDOWS_ET}", flush=True)
    while True:
        now = datetime.now(_ET)
        today_et = now.date().isoformat()
        if _in_window(now):
            path = OUT_DIR / f"{today_et}.jsonl"
            with path.open("a") as out:
                per_root = _sample_once(broker, today_et, out)
            for u, c in per_root.items():
                day_totals[u] = day_totals.get(u, 0) + c
            print(f"{now.isoformat(timespec='seconds')} sampled "
                  f"{sum(per_root.values())} rows", flush=True)
            time.sleep(SAMPLE_SECONDS)
            continue
        nxt = _next_window_start(now)
        if nxt is None:
            print("index_chain_sampler: past the close window — done for the day",
                  flush=True)
            print(f"day per-root totals: {day_totals}", flush=True)
            missing = [u for u in ALWAYS_EXPECTED if not day_totals.get(u)]
            if missing:
                print(f"ERROR day ended with ZERO rows for {missing} — the "
                      "study lost a session for those roots", flush=True)
            return
        wait_s = (nxt - _now_min(now)) * 60 - now.second
        print(f"index_chain_sampler: sleeping {wait_s}s until next window", flush=True)
        time.sleep(max(wait_s, 30))


if __name__ == "__main__":
    main()
