"""Fetch Alpaca daily bars for every underlying in the OPRA corpus.

Alpaca rather than Databento, decided by measurement on 2026-07-26: Databento
EQUS.SUMMARY matches Alpaca to the cent on every bar, but ships UNADJUSTED —
CRWD's 4:1 split reads 772.74 → 193.98 as a -75% "return" — whereas our fetch
already asks for ``adjustment=split``. Alpaca is free, verified against an
independent consolidated source, and hands us the adjustment. (EQUS.MINI, the
cheaper product, is a venue subset carrying ~3% of consolidated volume and would
have injected ~0.5% error into every ATR — it is not an option.)

Symbols Alpaca cannot serve — index roots (SPX/SPXW/VIX/NDX) and names delisted
since the session — are reported as a count, not silently dropped. That number
is the input to the "do we buy the $4.82 point-in-time backfill" decision.

  python tools/flow_corpus_bars.py --start 2025-07-28 --end 2026-07-25
  python tools/flow_corpus_bars.py --start ... --end ... --symbols BRK.B,BF.B,BLD

``--symbols`` skips the definition-file scan entirely and fetches exactly the
list given — the shape Addendum G obligation 2's targeted backfill needs (dotted
class-share forms, bar-less mapped successors, the SPY benchmark). Without it,
behaviour is unchanged.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import databento as db
import httpx
import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.flow_corpus import init_db, upsert_bars  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFS_DIR = ROOT / "data" / "databento" / "opra_defs"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
CHUNK = 200  # symbols per request

logger = logging.getLogger("flow_corpus_bars")


def _ticker_ok(sym: str) -> bool:
    """Alphanumeric, plus the dotted class-share forms.

    Was a bare `isalnum()`, which silently dropped BRK.B / BF.B / HEI.A / PBR.A —
    exactly the forms Addendum G obligation 2's backfill has to reach (bars
    stored under `BRK.B` are unreachable otherwise). Widened to the dot only:
    `$` is Nasdaq Trader's preferred-share marker and carries no options.
    """
    return bool(sym) and sym.replace(".", "").isalnum()


def _universe() -> list[str]:
    """Distinct option underlyings across every definitions file on disk."""
    syms: set[str] = set()
    files = sorted(DEFS_DIR.glob("*.dbn.zst"))
    for i, path in enumerate(files, 1):
        arr = db.DBNStore.from_file(path).to_ndarray()
        cls = arr["instrument_class"]
        arr = arr[(cls == b"C") | (cls == b"P")]
        syms.update(str(s) for s in np.unique(np.char.strip(
            arr["underlying"].astype("U16"))))
        if i % 20 == 0:
            logger.info("  scanned %d/%d definition files, %d symbols",
                        i, len(files), len(syms))
    return sorted(s for s in syms if _ticker_ok(s))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--symbols", help="comma-separated explicit list; skips the "
                                     "OPRA-definition universe scan entirely")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    load_dotenv(ROOT / ".env")
    init_db()

    if args.symbols:
        syms = sorted({s.strip().upper() for s in args.symbols.split(",") if s.strip()})
        logger.info("explicit symbol list: %d underlyings", len(syms))
    else:
        syms = _universe()
        logger.info("universe: %d underlyings", len(syms))

    headers = {
        "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
    }
    served: set[str] = set()
    rejected: list[str] = []
    total = 0

    def fetch(client: httpx.Client, group: list[str]) -> list[tuple]:
        """Batch fetch, bisecting on 400. Alpaca rejects the WHOLE request when a
        single symbol is unknown (`invalid symbol: /CL` zeroed 53 valid names'
        HV column on 2026-07-25), and an options-underlying universe is full of
        roots Alpaca cannot serve — indices, test tickers, delisted names. Halving
        isolates the offender in log n requests instead of losing the batch."""
        rows: list[tuple] = []
        token = None
        while True:
            params = {
                "symbols": ",".join(group), "timeframe": "1Day",
                "start": args.start, "end": args.end, "limit": 10000,
                # split-only: dividends must not shift the price level the
                # ATR is measured in (brokers/alpaca.py carries the same note)
                "adjustment": "split", "feed": "sip", "sort": "asc",
            }
            if token:
                params["page_token"] = token
            resp = client.get(BARS_URL, headers=headers, params=params)
            if resp.status_code == 400:
                if len(group) == 1:
                    rejected.append(group[0])
                    return []
                mid = len(group) // 2
                return fetch(client, group[:mid]) + fetch(client, group[mid:])
            resp.raise_for_status()
            payload = resp.json()
            for sym, bars in (payload.get("bars") or {}).items():
                served.add(sym)
                for b in bars:
                    rows.append((sym, b["t"][:10], b.get("o"), b.get("h"),
                                 b.get("l"), b.get("c"), b.get("v"),
                                 "split", "alpaca_sip"))
            token = payload.get("next_page_token")
            if not token:
                return rows

    with httpx.Client(timeout=90.0) as client:
        for i in range(0, len(syms), CHUNK):
            chunk = syms[i:i + CHUNK]
            try:
                rows = fetch(client, chunk)
            except Exception:
                logger.exception("chunk %d failed outright", i // CHUNK + 1)
                rows = []
            total += upsert_bars(rows)
            logger.info("chunk %d/%d: %d symbols → %d bars (cum %d, rejected %d)",
                        i // CHUNK + 1, (len(syms) + CHUNK - 1) // CHUNK,
                        len(chunk), len(rows), total, len(rejected))
            time.sleep(0.3)  # stay well inside the rate limit

    missing = sorted(set(syms) - served)
    logger.info("bars written: %d | served: %d | NOT served: %d (%.1f%%) | rejected-by-alpaca: %d",
                total, len(served), len(missing),
                100.0 * len(missing) / max(len(syms), 1), len(rejected))
    logger.info("unserved sample: %s", ", ".join(missing[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
