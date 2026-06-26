"""
momentum_reference.py — Residual-Momentum Target-Set Reference Implementation
==============================================================================

PURPOSE
-------
Canonical, dependency-light reference for the residual (idiosyncratic) momentum
portfolio strategy. Serv ports this logic to production; it MUST reproduce the
golden outputs in research/fixtures/momentum_targets.json exactly.

ALGORITHM (ported verbatim from sector_idio_mom_verify.py and
combined_book_nav.py::run_mom_sleeve() — do NOT redesign)
-----------------------------------------------------------

Parameters
~~~~~~~~~~
  MOM_LONG          = 252   trading days   lookback start ("12 months")
  MOM_SKIP          = 21    trading days   skip the most recent month ("12-1")
  QUINTILE_FRAC     = 0.20                 top 20% of eligible stocks selected
  MIN_SECTOR_STOCKS = 3                    minimum stocks in a sector to compute
                                           the sector average (sectors with fewer
                                           members are dropped; their stocks are
                                           excluded from selection)

Step 1 — Raw 12-1 momentum for symbol s at rank_date d
~~~~~~~
  ri       = index of d in s's chronologically-sorted date list
  idx_long = ri - MOM_LONG   (252 bars back)
  idx_skip = ri - MOM_SKIP   (21 bars back)
  mom_s    = (close[idx_skip] / close[idx_long]) - 1.0

  Eligibility gates:
    • s must have a bar on exactly rank_date (ri is not None)
    • idx_long >= 0  (enough history)
    • close[idx_long] > 0 AND close[idx_skip] > 0
    • s must not be a delisted stub (symbols matching SYM-YYYYMM are skipped
      when rank_date[:7] >= YYYY-MM)

Step 2 — Sector average momentum (equal-weight)
~~~~~~~
  Group eligible stocks by their sector from the sector_map.
  For each sector with >= MIN_SECTOR_STOCKS members:
    sec_avg[sector] = arithmetic mean of {mom_s for s in sector}
  Sectors with fewer members are discarded (their stocks cannot earn a residual).

Step 3 — Residual (idiosyncratic) momentum
~~~~~~~
  residual_s = mom_s - sec_avg[sector_of_s]
  Only computed for stocks whose sector has a valid sec_avg.

Step 4 — Top-quintile selection (equal-weight)
~~~~~~~
  Sort residuals descending.
  n_top = max(1, int(N_eligible * QUINTILE_FRAC))
  Top n_top stocks form the equal-weight portfolio for month T+1.

NO LOOK-AHEAD: only bars with date <= as_of (rank_date) are used. The caller
must supply bars pruned to date <= as_of, OR rely on the index lookup (ri for
rank_date) which naturally prevents future data from entering the calculation.

SECTOR MAP
----------
sector_map = {symbol: sector_string} loaded from
research/fixtures/momentum_sectors.json.
Exclusions already applied: Agriculture, Unknown, empty, NULL.

USAGE
-----
  from research.momentum_reference import compute_target_set, load_sector_map, load_bars_for_symbols

  sector_map = load_sector_map()
  bars = load_bars_for_symbols(list(sector_map))
  targets = compute_target_set(bars, sector_map, "2024-12-31")

  # Or from the command line:
  python -m research.momentum_reference --date 2024-12-31
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

# ── Strategy parameters ────────────────────────────────────────────────────────
MOM_LONG          = 252   # 12-month lookback in trading days
MOM_SKIP          = 21    # skip last ~1 month (12-1 style)
QUINTILE_FRAC     = 0.20  # top 20%
MIN_SECTOR_STOCKS = 3     # min sector members to compute a valid sector average

# ── Default paths (relative to repo root) ────────────────────────────────────
_THIS_DIR   = Path(__file__).parent
_REPO_ROOT  = _THIS_DIR.parent
BARS_DB     = _REPO_ROOT / "data" / "research" / "bars.db"
SECTOR_MAP_PATH = _THIS_DIR / "fixtures" / "momentum_sectors.json"

# ── Delist pattern ────────────────────────────────────────────────────────────
# Survivorship-free universe encodes delisted symbols as "TICKER-YYYYMM"
_DELIST_PAT = re.compile(r"^(.+)-(\d{6})$")


def _delist_month(sym: str) -> str | None:
    """Return 'YYYY-MM' delist month for stub symbols, else None."""
    m = _DELIST_PAT.match(sym)
    if m:
        ym = m.group(2)
        return f"{ym[:4]}-{ym[4:6]}"
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def load_sector_map(path: Path | str | None = None) -> dict[str, str]:
    """
    Load {symbol: sector} from momentum_sectors.json.

    The JSON encodes {symbol: {"sector": ..., "sic": ..., "sic_desc": ...}};
    this function returns the flat {symbol: sector_string} needed by
    compute_target_set().

    Parameters
    ----------
    path : optional override for fixture path (default: research/fixtures/momentum_sectors.json)
    """
    p = Path(path) if path else SECTOR_MAP_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {sym: info["sector"] for sym, info in raw["symbols"].items()}


def load_bars_for_symbols(
    symbols: list[str],
    bars_db: Path | str | None = None,
) -> dict[str, tuple[list[str], list[float], list[float]]]:
    """
    Load daily bars from bars.db for the given symbols.

    Returns
    -------
    dict mapping symbol -> (dates, closes, opens) where:
      dates  : list[str]   ISO dates sorted ascending
      closes : list[float] closing prices aligned to dates
      opens  : list[float] opening prices aligned to dates
    """
    db = Path(bars_db) if bars_db else BARS_DB
    placeholders = ",".join("?" * len(symbols))
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        f"SELECT symbol, date, o, c FROM daily_bars"
        f" WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
        list(symbols),
    ).fetchall()
    conn.close()

    raw: dict[str, list[tuple]] = defaultdict(list)
    for symbol, date, o, c in rows:
        raw[symbol].append((date, float(o), float(c)))

    result: dict[str, tuple[list[str], list[float], list[float]]] = {}
    for sym, bar_list in raw.items():
        bar_list.sort(key=lambda x: x[0])
        result[sym] = (
            [b[0] for b in bar_list],    # dates
            [b[2] for b in bar_list],    # closes
            [b[1] for b in bar_list],    # opens
        )
    return result


def compute_target_set(
    bars: dict[str, tuple[list[str], list[float], list[float]]],
    sector_map: dict[str, str],
    as_of: str,
) -> list[dict]:
    """
    Compute the residual-momentum target set as of `as_of`.

    Parameters
    ----------
    bars : {symbol: (dates, closes, opens)} from load_bars_for_symbols().
           The algorithm uses only bars with date <= as_of; no look-ahead.
    sector_map : {symbol: sector_string} from load_sector_map().
                 Agriculture / Unknown / empty already excluded.
    as_of : ISO date string — the rank date (last trading day of month T).
            Must be an exact trading day present in bars["SPY"] or a liquid
            representative symbol; stocks that have no bar on this exact date
            are skipped (they were not yet listed or were delisted).

    Returns
    -------
    list of dicts for the TOP QUINTILE only, sorted by residual_momentum DESC:
        [{"symbol": str, "residual_momentum": float}, ...]
    Returns [] if fewer than 10 stocks are eligible (degenerate period).

    Notes
    -----
    • Equal-weight — caller allocates 1/N to each symbol in the returned list.
    • Deterministic: no randomness. Ties in residual_momentum are broken by
      the sort's stable ordering (original dict iteration order from stock_mom,
      which reflects bars insertion order from the DB; this matches the
      validated research code).
    """
    # Build per-symbol date index (one pass, reused across all lookups)
    sym_closes: dict[str, list[float]]    = {}
    sym_dmap:   dict[str, dict[str, int]] = {}

    for sym in sector_map:
        if sym not in bars:
            continue
        dates, closes, _opens = bars[sym]
        sym_closes[sym] = closes
        sym_dmap[sym]   = {d: i for i, d in enumerate(dates)}

    # ── Step 1: raw 12-1 momentum ────────────────────────────────────────────
    stock_mom: dict[str, float] = {}

    for sym in sector_map:
        # Skip delisted stubs past their delist month
        dm = _delist_month(sym)
        if dm is not None and as_of[:7] >= dm:
            continue

        dmap = sym_dmap.get(sym)
        if dmap is None:
            continue
        ri = dmap.get(as_of)
        if ri is None:
            continue  # symbol had no bar on rank_date

        idx_long = ri - MOM_LONG   # 252 bars back
        idx_skip = ri - MOM_SKIP   # 21 bars back
        if idx_long < 0 or idx_skip < 0:
            continue  # insufficient history

        closes = sym_closes[sym]
        c_long = closes[idx_long]
        c_skip = closes[idx_skip]
        if c_long <= 0 or c_skip <= 0:
            continue  # bad price data

        stock_mom[sym] = (c_skip / c_long) - 1.0

    if len(stock_mom) < 10:
        return []  # degenerate period — not enough stocks

    # ── Step 2: sector average momentum (equal-weight) ───────────────────────
    sector_buckets: dict[str, list[float]] = defaultdict(list)
    for sym, mom in stock_mom.items():
        sector_buckets[sector_map[sym]].append(mom)

    sector_avg: dict[str, float] = {
        sec: float(np.mean(vals))
        for sec, vals in sector_buckets.items()
        if len(vals) >= MIN_SECTOR_STOCKS
    }

    # ── Step 3: idiosyncratic (residual) momentum ────────────────────────────
    idio: list[tuple[float, str]] = []
    for sym, mom in stock_mom.items():
        sec = sector_map[sym]
        if sec not in sector_avg:
            continue  # sector too small to have a valid average
        idio.append((mom - sector_avg[sec], sym))

    if len(idio) < 10:
        return []

    # ── Step 4: top-quintile selection (equal-weight) ────────────────────────
    idio.sort(key=lambda x: x[0], reverse=True)
    n_top = max(1, int(len(idio) * QUINTILE_FRAC))
    selected = idio[:n_top]

    return [
        {"symbol": sym, "residual_momentum": float(score)}
        for score, sym in selected
    ]


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute residual-momentum target set for a given month-end date."
    )
    parser.add_argument(
        "--date", required=True,
        help="As-of date (YYYY-MM-DD) — must be the last trading day of the month.",
    )
    parser.add_argument("--bars-db",    default=None, help="Override path to bars.db")
    parser.add_argument("--sector-map", default=None, help="Override path to momentum_sectors.json")
    args = parser.parse_args()

    sector_map = load_sector_map(args.sector_map)
    print(f"Sector map loaded: {len(sector_map)} symbols")

    all_syms = list(sector_map.keys())
    print(f"Loading bars for {len(all_syms)} symbols ...", flush=True)
    bars = load_bars_for_symbols(all_syms, args.bars_db)
    print(f"Bars loaded: {len(bars)} symbols have data")

    target_set = compute_target_set(bars, sector_map, args.date)

    if not target_set:
        print(f"\nNo target set produced for {args.date} (insufficient eligible stocks).")
        return

    print(f"\nTarget set as of {args.date}: {len(target_set)} stocks (equal-weight)")
    print(f"{'Symbol':<10}  {'Residual Momentum':>18}")
    print("-" * 32)
    for entry in target_set:
        print(f"{entry['symbol']:<10}  {entry['residual_momentum']:+18.6f}")


if __name__ == "__main__":
    _main()
