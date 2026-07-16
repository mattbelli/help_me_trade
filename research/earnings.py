"""SEC EDGAR earnings-announcement dates from 8-K Item 2.02 filings."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from db.base import connect
from research.bars import DEFAULT_BARS_DB_PATH, RESEARCH_DATA_DIR, cached_symbols
from research.fundamentals import (
    DEFAULT_FUNDAMENTALS_DB_PATH,
    DEFAULT_USER_AGENT,
    SUBMISSIONS_URL,
    _base_ticker,
    _load_ticker_cik_map,
)

logger = logging.getLogger(__name__)

DEFAULT_EARNINGS_DB_PATH = RESEARCH_DATA_DIR / "earnings.db"
OLDER_SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"

_REQUEST_INTERVAL_SEC = 0.13  # ~7–8 req/sec, polite EDGAR throttle

_CREATE_EARNINGS = """
CREATE TABLE IF NOT EXISTS earnings (
    symbol TEXT NOT NULL,
    cik TEXT NOT NULL,
    date TEXT NOT NULL,
    accepted_at TEXT,
    PRIMARY KEY (cik, date)
)
"""

_CREATE_EARNINGS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_earnings_symbol ON earnings (symbol)
"""


def _ensure_schema(conn) -> None:
    conn.execute(_CREATE_EARNINGS)
    conn.execute(_CREATE_EARNINGS_INDEX)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(earnings)")}
    if "accepted_at" not in cols:
        conn.execute("ALTER TABLE earnings ADD COLUMN accepted_at TEXT")
    conn.commit()


def _parse_earnings_from_submissions(recent: dict) -> list[tuple[str, str | None]]:
    """Given a filings.recent-style dict, return 8-K Item-2.02 (filingDate, acceptanceDateTime).

    The raw ISO acceptance stamp is stored, NOT a derived bmo/amc label — the label
    bakes in a threshold guess; the raw stamp can be re-derived under stricter rules
    without a refetch (see EARNINGS_DRIFT_EXTRA_DAY_EXPERIMENT.md).
    """
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    items_list = recent.get("items") or []
    accepted_list = recent.get("acceptanceDateTime") or []

    rows: list[tuple[str, str | None]] = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        items = items_list[i] if i < len(items_list) else None
        if "2.02" not in (items or ""):
            continue
        if i < len(filing_dates):
            accepted = accepted_list[i] if i < len(accepted_list) else None
            rows.append((str(filing_dates[i]), str(accepted) if accepted else None))
    return rows


def _resolve_cik(
    symbol: str,
    ticker_cik: dict[str, str],
    *,
    fundamentals_conn=None,
) -> str | None:
    """Map symbol to CIK via fundamentals cache or SEC ticker map (delisted suffix stripped)."""
    sym = symbol.strip().upper()
    if fundamentals_conn is not None:
        row = fundamentals_conn.execute(
            "SELECT cik FROM companies WHERE symbol = ?",
            (sym,),
        ).fetchone()
        if row:
            return str(row["cik"])
    base = _base_ticker(sym)
    return ticker_cik.get(base)


def _fetch_submission_blocks(client, cik: str) -> list[dict]:
    """Fetch main submissions JSON plus older filing blocks for a CIK."""
    url = SUBMISSIONS_URL.format(cik=int(cik))
    response = client.get(url)
    response.raise_for_status()
    payload = response.json()

    blocks: list[dict] = []
    filings = payload.get("filings") or {}
    recent = filings.get("recent")
    if recent:
        blocks.append(recent)

    for file_entry in filings.get("files") or []:
        name = file_entry.get("name")
        if not name:
            continue
        time.sleep(_REQUEST_INTERVAL_SEC)
        file_response = client.get(OLDER_SUBMISSIONS_URL.format(name=name))
        file_response.raise_for_status()
        blocks.append(file_response.json())

    return blocks


def _collect_earnings_dates(client, cik: str) -> list[tuple[str, str | None]]:
    """Fetch all submission blocks and extract unique 8-K Item-2.02 (date, accepted_at).

    Same-date duplicates (amendments/refilings) keep the EARLIEST acceptance stamp —
    the original release is what timed the announcement.
    """
    by_date: dict[str, str | None] = {}
    for block in _fetch_submission_blocks(client, cik):
        for d, accepted in _parse_earnings_from_submissions(block):
            prior = by_date.get(d)
            if d not in by_date or (accepted and (prior is None or accepted < prior)):
                by_date[d] = accepted
    return sorted(by_date.items())


def _upsert_earnings(conn, symbol: str, cik: str, dates: list[tuple[str, str | None]]) -> None:
    if not dates:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO earnings (symbol, cik, date, accepted_at)
        VALUES (?, ?, ?, ?)
        """,
        [(symbol, cik, d, accepted) for d, accepted in dates],
    )


def _coverage_report(
    symbols: list[str],
    *,
    cache_db: Path,
) -> dict:
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        resolved = 0
        unresolved_symbols: list[str] = []
        total_dates = 0
        for sym in symbols:
            sym_upper = sym.strip().upper()
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM earnings WHERE symbol = ?",
                (sym_upper,),
            ).fetchone()
            cnt = int(row["cnt"])
            total_dates += cnt
            if cnt >= 1:
                resolved += 1
            else:
                unresolved_symbols.append(sym_upper)
    finally:
        conn.close()

    return {
        "requested": len(symbols),
        "resolved": resolved,
        "unresolved": len(unresolved_symbols),
        "total_dates": total_dates,
        "unresolved_symbols": unresolved_symbols[:50],
    }


def build_earnings(
    symbols: list[str] | None = None,
    *,
    cache_db: Path = DEFAULT_EARNINGS_DB_PATH,
    fundamentals_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    bars_db: Path = DEFAULT_BARS_DB_PATH,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    """Fetch EDGAR 8-K Item-2.02 dates per symbol; return coverage summary."""
    import httpx

    if symbols is None:
        symbols = cached_symbols("norgate", db_path=bars_db)

    conn = connect(cache_db)
    fund_conn = connect(fundamentals_db)
    try:
        _ensure_schema(conn)
        with httpx.Client(
            timeout=60.0,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        ) as client:
            ticker_cik = _load_ticker_cik_map(client)
            time.sleep(_REQUEST_INTERVAL_SEC)

            for symbol in symbols:
                cik = _resolve_cik(symbol, ticker_cik, fundamentals_conn=fund_conn)
                if not cik:
                    logger.debug("No CIK for %s", symbol)
                    continue

                sym_upper = symbol.strip().upper()
                try:
                    dates = _collect_earnings_dates(client, cik)
                    time.sleep(_REQUEST_INTERVAL_SEC)
                    _upsert_earnings(conn, sym_upper, cik, dates)
                    logger.info(
                        "Cached %d earnings dates for %s (CIK %s)",
                        len(dates),
                        symbol,
                        cik,
                    )
                except Exception as exc:  # noqa: BLE001 - resilience over strictness
                    logger.warning(
                        "  %s (CIK %s): fetch failed (%s) — skipping",
                        symbol,
                        cik,
                        str(exc)[:80],
                    )
                    continue

            conn.commit()
    finally:
        conn.close()
        fund_conn.close()

    report = _coverage_report(symbols, cache_db=cache_db)
    logger.warning(
        "Earnings coverage: %d/%d resolved, %d unresolved, %d total dates "
        "(current EDGAR ticker map — delisted names are expected gaps)",
        report["resolved"],
        report["requested"],
        report["unresolved"],
        report["total_dates"],
    )
    if report["unresolved_symbols"]:
        logger.warning(
            "Sample unresolved symbols: %s",
            ", ".join(report["unresolved_symbols"][:10]),
        )
    return report


def earnings_dates(
    symbol: str,
    *,
    cache_db: Path = DEFAULT_EARNINGS_DB_PATH,
) -> list[str]:
    """Return that symbol's earnings dates sorted ascending (ISO 'YYYY-MM-DD')."""
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT date FROM earnings WHERE symbol = ? ORDER BY date ASC",
            (symbol.strip().upper(),),
        ).fetchall()
        return [row["date"] for row in rows]
    finally:
        conn.close()



def compute_reaction_pct(
    earnings_date: str,
    bars: list,
) -> float | None:
    """Compute 1-day price reaction % for one earnings date given a sorted bar list.

    Bars must be sorted ascending by .time (ISO date string).  Returns None when
    there are insufficient bars to compute the reaction (e.g. earnings date is before
    the bar history, or there is no subsequent trading day in the data).

    The reaction is defined as:
        (close_of_first_bar_on_or_after_date - close_of_bar_immediately_before) /
        close_of_bar_immediately_before * 100

    Earnings typically print after the close, so the *next* session's open reflects
    the reaction; using the bar on/after date vs. the bar immediately before captures
    that cleanly regardless of whether the report was pre-open or after-close.
    """
    # Find the index of the first bar whose date >= earnings_date
    after_idx: int | None = None
    for i, bar in enumerate(bars):
        bar_date = bar.time[:10]  # ISO date prefix
        if bar_date >= earnings_date:
            after_idx = i
            break

    if after_idx is None or after_idx == 0:
        # No bar on/after the date, or no prior bar to compare against
        return None

    before_close = bars[after_idx - 1].close
    after_close = bars[after_idx].close
    if before_close == 0:
        return None
    return round((after_close - before_close) / before_close * 100, 2)


def earnings_history_reactions(
    symbol: str,
    bars: list,
    *,
    n: int = 6,
    cache_db: Path = DEFAULT_EARNINGS_DB_PATH,
) -> list[dict]:
    """Return the last N earnings events with their 1-day price reaction %.

    Args:
        symbol: Ticker symbol.
        bars: Daily bars sorted ascending by .time (from aget_stock_bars).
        n: Max number of most-recent events to return.
        cache_db: Path to the EDGAR earnings SQLite cache.

    Returns:
        List of {"date": "YYYY-MM-DD", "reaction_pct": float} dicts, most recent
        first, only including events where the reaction could be computed.
    """
    all_dates = earnings_dates(symbol, cache_db=cache_db)
    # Take the last n dates (most recent) and compute reactions
    recent_dates = all_dates[-n:] if len(all_dates) > n else all_dates

    results: list[dict] = []
    for d in recent_dates:
        pct = compute_reaction_pct(d, bars)
        if pct is not None:
            results.append({"date": d, "reaction_pct": pct})

    results.reverse()  # most recent first
    return results


def _print_coverage(report: dict) -> None:
    print("=== Earnings coverage (EDGAR ticker map is current-only) ===")
    print(f"requested:    {report['requested']}")
    print(f"resolved:     {report['resolved']}")
    print(f"unresolved:   {report['unresolved']}")
    print(f"total_dates:  {report['total_dates']}")
    if report.get("unresolved_symbols"):
        print("sample unresolved (survivor-gap — do not silently drop in filters):")
        for sym in report["unresolved_symbols"]:
            print(f"  {sym}")


def _lookup_symbol(symbol: str, *, cache_db: Path) -> None:
    dates = earnings_dates(symbol, cache_db=cache_db)
    sym = symbol.strip().upper()
    if not dates:
        print(f"{sym}: no earnings dates cached")
        return
    print(f"symbol: {sym}")
    print(f"count:  {len(dates)}")
    print(f"range:  {dates[0]} .. {dates[-1]}")
    print("recent:")
    for d in dates[-5:]:
        print(f"  {d}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SEC EDGAR earnings dates from 8-K Item 2.02 filings"
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Fetch EDGAR earnings dates for cached Norgate symbols and print coverage",
    )
    parser.add_argument(
        "--lookup",
        metavar="SYMBOL",
        help="Print cached earnings dates for a symbol",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_EARNINGS_DB_PATH,
        help="Earnings cache DB path",
    )
    parser.add_argument(
        "--bars-db",
        type=Path,
        default=DEFAULT_BARS_DB_PATH,
        help="Daily bars DB path (for symbol list)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    if args.build:
        report = build_earnings(cache_db=args.cache_db, bars_db=args.bars_db)
        _print_coverage(report)

    if args.lookup:
        _lookup_symbol(args.lookup, cache_db=args.cache_db)

    if not (args.build or args.lookup):
        _parse_args(["--help"])


if __name__ == "__main__":
    main()
