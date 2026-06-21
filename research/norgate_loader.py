"""Norgate Data → bars.db ingest (OFFLINE, Windows-only — requires NDU running).

This is the survivorship-free data path for the decision-engine backtest harness.
It runs on the founder's Windows PC where the Norgate Data Updater (NDU) serves data
to the `norgatedata` Python package. It fetches split/dividend-adjusted daily bars for
a (delisting-inclusive) universe and upserts them into the source-agnostic bar cache
with source="norgate". The rest of the pipeline (dataset → train) is unchanged.

Survivorship-free rule: use a *"Current & Past"* watchlist (includes delisted symbols).
A "current constituents" list silently reintroduces survivorship bias.

Raw bars stay under gitignored data/; only the trained JSON artifacts get committed.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

from research.bars import DEFAULT_BARS_DB_PATH, cache_bars
from signals.trade_context import DatedBar

logger = logging.getLogger(__name__)

NORGATE_SOURCE = "norgate"


def _require_norgatedata():
    """Import norgatedata lazily so the rest of the repo (serv/Linux) never needs it."""
    try:
        import norgatedata  # type: ignore
    except ImportError as exc:  # pragma: no cover - Windows-only dependency
        raise RuntimeError(
            "norgatedata is not installed. Run this on the Windows PC with the "
            "Norgate Data Updater (NDU) running: pip install norgatedata"
        ) from exc
    return norgatedata


def fetch_norgate_bars(symbol: str, start: date, end: date) -> list[DatedBar]:
    """Fetch total-return-adjusted daily bars for one symbol via norgatedata.

    TOTALRETURN adjustment folds splits + dividends into the price series, which is
    the correct basis for triple-barrier return labels and the ATR/RS features.
    """
    nd = _require_norgatedata()
    df = nd.price_timeseries(
        symbol,
        stock_price_adjustment_setting=nd.StockPriceAdjustmentType.TOTALRETURN,
        padding_setting=nd.PaddingType.NONE,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        timeseriesformat="pandas-dataframe",
    )
    if df is None or df.empty:
        return []

    bars: list[DatedBar] = []
    for ts, row in df.iterrows():
        bar_date = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
        bars.append(
            {
                "d": bar_date.isoformat(),
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
                "v": float(row["Volume"]),
            }
        )
    bars.sort(key=lambda b: b["d"])
    return bars


def resolve_universe(
    *, watchlist: str | None, symbols: list[str] | None
) -> list[str]:
    """Resolve the symbol universe from a Norgate watchlist and/or an explicit list."""
    out: list[str] = list(symbols or [])
    if watchlist:
        nd = _require_norgatedata()
        wl_symbols = nd.watchlist_symbols(watchlist)
        logger.info("Watchlist %r → %d symbols", watchlist, len(wl_symbols))
        out.extend(wl_symbols)
    # de-dup, preserve order, normalize
    seen: set[str] = set()
    universe: list[str] = []
    for sym in out:
        norm = sym.strip().upper()
        if norm and norm not in seen:
            seen.add(norm)
            universe.append(norm)
    return universe


def ingest(
    symbols: list[str],
    start: date,
    end: date,
    *,
    bars_db_path: Path = DEFAULT_BARS_DB_PATH,
) -> dict:
    """Fetch + cache norgate bars for every symbol. Returns a summary dict."""
    bars_db_path.parent.mkdir(parents=True, exist_ok=True)
    total_symbols = 0
    total_bars = 0
    skipped: list[str] = []
    for i, symbol in enumerate(symbols, 1):
        try:
            bars = fetch_norgate_bars(symbol, start, end)
        except Exception as exc:  # one bad symbol shouldn't kill a 3000-symbol run
            logger.warning("  %s: fetch failed (%s)", symbol, exc)
            skipped.append(symbol)
            continue
        if not bars:
            skipped.append(symbol)
            continue
        cache_bars(symbol, bars, source=NORGATE_SOURCE, db_path=bars_db_path)
        total_symbols += 1
        total_bars += len(bars)
        if i % 100 == 0:
            logger.info("  ...%d/%d symbols, %d bars cached", i, len(symbols), total_bars)
    summary = {
        "symbols_requested": len(symbols),
        "symbols_cached": total_symbols,
        "symbols_skipped": len(skipped),
        "bars_cached": total_bars,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "source": NORGATE_SOURCE,
        "db_path": str(bars_db_path),
    }
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Norgate daily bars → bars.db")
    parser.add_argument(
        "--watchlist",
        help='Norgate watchlist name (use a "Current & Past" list for '
        'survivorship-free coverage, e.g. "Russell 3000 Current & Past")',
    )
    parser.add_argument(
        "--symbols",
        help="Optional comma-separated extra symbols (merged with --watchlist)",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=15,
        help="Years of history ending today (default: 15)",
    )
    args = parser.parse_args(argv)
    if not args.watchlist and not args.symbols:
        parser.error("provide --watchlist and/or --symbols")
    return args


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    symbols = resolve_universe(
        watchlist=args.watchlist,
        symbols=[s for s in (args.symbols or "").split(",") if s.strip()],
    )
    end = date.today()
    start = end - timedelta(days=args.years * 365)
    logger.info("Ingesting %d symbols, %s..%s", len(symbols), start, end)
    summary = ingest(symbols, start, end)
    logger.info("Norgate ingest complete: %s", summary)


if __name__ == "__main__":
    main()
