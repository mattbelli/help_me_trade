"""SEC EDGAR fundamentals cache — point-in-time shares, SIC sector, market cap."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from db.base import connect
from research.bars import DEFAULT_BARS_DB_PATH, RESEARCH_DATA_DIR, cached_symbols

logger = logging.getLogger(__name__)

DEFAULT_FUNDAMENTALS_DB_PATH = RESEARCH_DATA_DIR / "fundamentals.db"
DEFAULT_USER_AGENT = "help_me_trade-research matt.belliveau@gmail.com"

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SHARES_PRIMARY_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/dei/"
    "EntityCommonStockSharesOutstanding.json"
)
SHARES_FALLBACK_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/"
    "CommonStockSharesOutstanding.json"
)

_REQUEST_INTERVAL_SEC = 0.13  # ~7–8 req/sec, polite EDGAR throttle

_CREATE_COMPANIES = """
CREATE TABLE IF NOT EXISTS companies (
    symbol TEXT NOT NULL PRIMARY KEY,
    cik TEXT NOT NULL,
    sic INTEGER,
    sic_desc TEXT,
    sector TEXT
)
"""

_CREATE_SHARES = """
CREATE TABLE IF NOT EXISTS shares (
    cik TEXT NOT NULL,
    date TEXT NOT NULL,
    shares REAL NOT NULL,
    PRIMARY KEY (cik, date)
)
"""

_CREATE_SHARES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_shares_cik_date ON shares (cik, date)
"""


def _ensure_schema(conn) -> None:
    conn.execute(_CREATE_COMPANIES)
    conn.execute(_CREATE_SHARES)
    conn.execute(_CREATE_SHARES_INDEX)
    conn.commit()


def _base_ticker(symbol: str) -> str:
    """Strip Norgate delisted suffix (e.g. XLNX-202202 -> XLNX) for EDGAR lookup."""
    sym = symbol.strip().upper()
    if "-" in sym:
        base, suffix = sym.rsplit("-", 1)
        if suffix.isdigit():
            return base
    return sym


def sic_to_sector(sic: int | str | None) -> str:
    """Coarse sector bucket from SIC division code."""
    if sic is None:
        return "Unknown"
    code = int(sic)
    if 100 <= code <= 999:
        return "Agriculture"
    if 1000 <= code <= 1499:
        return "Mining"
    if 2000 <= code <= 3999:
        return "Manufacturing"
    if 4000 <= code <= 4999:
        return "Utilities/Transport"
    if 5000 <= code <= 5199:
        return "Wholesale"
    if 5200 <= code <= 5999:
        return "Retail"
    if 6000 <= code <= 6799:
        return "Finance"
    if 7000 <= code <= 8999:
        return "Services"
    return "Other"


def _parse_submissions(json_dict: dict) -> tuple[int | None, str | None]:
    sic_raw = json_dict.get("sic")
    sic_desc = json_dict.get("sicDescription")
    sic: int | None
    if sic_raw is None or sic_raw == "":
        sic = None
    else:
        sic = int(sic_raw)
    if sic_desc is not None:
        sic_desc = str(sic_desc)
    return sic, sic_desc


def _parse_shares(json_dict: dict) -> list[tuple[str, float]]:
    units = json_dict.get("units") or {}
    rows = units.get("shares") or []
    parsed: list[tuple[str, float]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        end = item.get("end")
        val = item.get("val")
        if end is None or val is None:
            continue
        parsed.append((str(end), float(val)))
    return parsed


def _load_ticker_cik_map(client) -> dict[str, str]:
    response = client.get(COMPANY_TICKERS_URL)
    response.raise_for_status()
    payload = response.json()
    mapping: dict[str, str] = {}
    for entry in payload.values():
        ticker = str(entry["ticker"]).strip().upper()
        cik = str(entry["cik_str"]).strip()
        mapping[ticker] = cik
    return mapping


def _fetch_submissions(client, cik: str) -> tuple[int | None, str | None]:
    url = SUBMISSIONS_URL.format(cik=int(cik))
    response = client.get(url)
    response.raise_for_status()
    return _parse_submissions(response.json())


def _fetch_shares(client, cik: str) -> list[tuple[str, float]]:
    url = SHARES_PRIMARY_URL.format(cik=int(cik))
    response = client.get(url)
    if response.status_code == 404:
        url = SHARES_FALLBACK_URL.format(cik=int(cik))
        response = client.get(url)
    if response.status_code == 404:
        # Company reports no recognized shares-outstanding XBRL concept — common.
        # Treat as no shares data (coverage gap), not an error.
        return []
    response.raise_for_status()
    return _parse_shares(response.json())


def _upsert_company(
    conn,
    *,
    symbol: str,
    cik: str,
    sic: int | None,
    sic_desc: str | None,
) -> None:
    sector_name = sic_to_sector(sic)
    conn.execute(
        """
        INSERT OR REPLACE INTO companies (symbol, cik, sic, sic_desc, sector)
        VALUES (?, ?, ?, ?, ?)
        """,
        (symbol, cik, sic, sic_desc, sector_name),
    )


def _upsert_shares(conn, cik: str, rows: list[tuple[str, float]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO shares (cik, date, shares)
        VALUES (?, ?, ?)
        """,
        [(cik, d, shares) for d, shares in rows],
    )


def coverage_report(
    symbols: list[str] | None = None,
    *,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    bars_db: Path = DEFAULT_BARS_DB_PATH,
) -> dict:
    """Compare requested symbols against cached companies rows."""
    if symbols is None:
        symbols = cached_symbols("norgate", db_path=bars_db)
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        cached = {
            row["symbol"]
            for row in conn.execute("SELECT symbol FROM companies").fetchall()
        }
    finally:
        conn.close()

    unresolved = [sym for sym in symbols if sym not in cached]
    resolved = len(symbols) - len(unresolved)
    return {
        "requested": len(symbols),
        "resolved": resolved,
        "unresolved": len(unresolved),
        "unresolved_symbols": unresolved[:50],
    }


def build_fundamentals(
    symbols: list[str] | None = None,
    *,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    bars_db: Path = DEFAULT_BARS_DB_PATH,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    """Fetch EDGAR SIC + shares history for symbols; return coverage summary."""
    import httpx

    if symbols is None:
        symbols = cached_symbols("norgate", db_path=bars_db)

    conn = connect(cache_db)
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
                base = _base_ticker(symbol)
                cik = ticker_cik.get(base)
                if not cik:
                    logger.debug("No CIK for %s (base ticker %s)", symbol, base)
                    continue

                # One bad symbol must not kill a 900+ symbol run (network blips,
                # unexpected 4xx/5xx, parse errors) — log and continue.
                try:
                    sic, sic_desc = _fetch_submissions(client, cik)
                    time.sleep(_REQUEST_INTERVAL_SEC)

                    share_rows = _fetch_shares(client, cik)
                    time.sleep(_REQUEST_INTERVAL_SEC)

                    _upsert_company(
                        conn,
                        symbol=symbol.strip().upper(),
                        cik=cik,
                        sic=sic,
                        sic_desc=sic_desc,
                    )
                    _upsert_shares(conn, cik, share_rows)
                    logger.info("Cached fundamentals for %s (CIK %s)", symbol, cik)
                except Exception as exc:  # noqa: BLE001 - resilience over strictness
                    logger.warning("  %s (CIK %s): fetch failed (%s) — skipping",
                                   symbol, cik, str(exc)[:80])
                    continue

            conn.commit()
    finally:
        conn.close()

    report = coverage_report(symbols, cache_db=cache_db, bars_db=bars_db)
    logger.warning(
        "Fundamentals coverage: %d/%d resolved, %d unresolved "
        "(current EDGAR ticker map — delisted names are expected gaps)",
        report["resolved"],
        report["requested"],
        report["unresolved"],
    )
    if report["unresolved_symbols"]:
        logger.warning(
            "Sample unresolved symbols: %s",
            ", ".join(report["unresolved_symbols"][:10]),
        )
    return report


def _company_row(conn, symbol: str):
    return conn.execute(
        "SELECT symbol, cik, sic, sic_desc, sector FROM companies WHERE symbol = ?",
        (symbol.strip().upper(),),
    ).fetchone()


def _shares_asof(conn, cik: str, d: str) -> float | None:
    row = conn.execute(
        """
        SELECT shares FROM shares
        WHERE cik = ? AND date <= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (cik, d),
    ).fetchone()
    return float(row["shares"]) if row else None


def _latest_shares(conn, cik: str) -> tuple[str, float] | None:
    row = conn.execute(
        """
        SELECT date, shares FROM shares
        WHERE cik = ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (cik,),
    ).fetchone()
    if row is None:
        return None
    return row["date"], float(row["shares"])


def market_cap(
    symbol: str,
    d: str,
    price: float,
    *,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
) -> float | None:
    """Point-in-time cap = price × most recent shares with end_date <= d."""
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        company = _company_row(conn, symbol)
        if company is None:
            return None
        shares = _shares_asof(conn, company["cik"], d)
        if shares is None:
            return None
        return price * shares
    finally:
        conn.close()


def sector(
    symbol: str,
    *,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
) -> str | None:
    """Cached coarse sector for the symbol (None if unresolved)."""
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        company = _company_row(conn, symbol)
        if company is None:
            return None
        return company["sector"]
    finally:
        conn.close()


def _print_coverage(report: dict) -> None:
    print("=== Fundamentals coverage (EDGAR ticker map is current-only) ===")
    print(f"requested:  {report['requested']}")
    print(f"resolved:   {report['resolved']}")
    print(f"unresolved: {report['unresolved']}")
    if report["unresolved_symbols"]:
        print("sample unresolved (survivor-gap — do not silently drop in cap filters):")
        for sym in report["unresolved_symbols"]:
            print(f"  {sym}")


def _lookup_symbol(symbol: str, *, cache_db: Path) -> None:
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        company = _company_row(conn, symbol)
        if company is None:
            print(f"{symbol}: not in cache (unresolved — likely delisted / no current CIK)")
            return
        latest = _latest_shares(conn, company["cik"])
        print(f"symbol: {company['symbol']}")
        print(f"cik:    {company['cik']}")
        print(f"sic:    {company['sic']}")
        print(f"sic_desc: {company['sic_desc']}")
        print(f"sector: {company['sector']}")
        if latest:
            print(f"latest_shares: {latest[1]:,.0f} (as of {latest[0]})")
        else:
            print("latest_shares: (none cached)")
    finally:
        conn.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SEC EDGAR fundamentals cache (market cap + sector)"
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Fetch EDGAR data for cached Norgate symbols and print coverage",
    )
    parser.add_argument("--lookup", metavar="SYMBOL", help="Print cached company details")
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Print resolved/unresolved counts from cache vs Norgate symbols",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_FUNDAMENTALS_DB_PATH,
        help="Fundamentals cache DB path",
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
        report = build_fundamentals(cache_db=args.cache_db, bars_db=args.bars_db)
        _print_coverage(report)

    if args.lookup:
        _lookup_symbol(args.lookup, cache_db=args.cache_db)

    if args.coverage:
        report = coverage_report(cache_db=args.cache_db, bars_db=args.bars_db)
        _print_coverage(report)

    if not (args.build or args.lookup or args.coverage):
        _parse_args(["--help"])


if __name__ == "__main__":
    main()
