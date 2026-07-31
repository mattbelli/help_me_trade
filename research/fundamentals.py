"""SEC EDGAR fundamentals cache — point-in-time shares, SIC sector, market cap."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date as _date
from pathlib import Path

from db.base import connect
from research.bars import DEFAULT_BARS_DB_PATH, RESEARCH_DATA_DIR, cached_symbols
from research.edgar_bulk import (
    DEFAULT_FACTS_DB_PATH,
    concept_interner,
    ensure_bulk_schema,
    fact_rows,
    upsert_facts,
)

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
# One request returns every XBRL concept a company has ever filed — far cheaper
# than a companyconcept call per metric.
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_REQUEST_INTERVAL_SEC = 0.13  # ~7–8 req/sec, polite EDGAR throttle

# Canonical metric -> the us-gaap tags that carry it, in preference order.
# Filers are inconsistent (revenue especially), so we normalize on the way in
# and keep the source tag on every row so a number can be traced back.
FINANCIAL_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "rnd_expense": ("ResearchAndDevelopmentExpense",),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
}

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


def _fetch_company_facts(client, cik: str) -> dict | None:
    response = client.get(COMPANY_FACTS_URL.format(cik=int(cik)))
    if response.status_code == 404:
        # No XBRL facts filed (common for trusts/ETFs) — a coverage gap, not an error.
        return None
    response.raise_for_status()
    return response.json()


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


def _period_bucket(period_start: str, period_end: str, fp: str | None) -> str | None:
    """Classify a period as "annual", "quarterly", or None, by its actual LENGTH.

    Not from `form`/`fp`: a 10-Q carries year-to-date and trailing-12-month
    durations alongside the quarter, so trusting the form files a $58B "quarterly"
    net income (measured on NVDA, 2026-04-26). Balance-sheet concepts are instant
    (no start) and fall back to the fiscal-period label.
    """
    if not period_start:
        return "annual" if str(fp or "").upper() == "FY" else "quarterly"
    try:
        days = (
            _date.fromisoformat(period_end) - _date.fromisoformat(period_start)
        ).days
    except ValueError:
        return None
    if 300 <= days <= 400:
        return "annual"
    if 60 <= days <= 120:
        return "quarterly"
    return None


def build_financials(
    symbols: list[str],
    *,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    facts_db: Path = DEFAULT_FACTS_DB_PATH,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    """Top up the corpus for `symbols` from the per-symbol API. Returns row counts.

    The bulk zip (`research.edgar_bulk`) is the corpus; this is the freshness
    path between nightly rebuilds — a 10-Q filed this morning is in the
    per-symbol endpoint hours before it is in the zip. Both writers share
    `fact_rows`/`upsert_facts`, so a top-up row is byte-identical to a bulk one
    and simply replaces it.
    """
    import httpx

    written: dict[str, int] = {}
    conn = connect(cache_db)
    facts_conn = connect(facts_db)
    try:
        _ensure_schema(conn)
        ensure_bulk_schema(facts_conn)
        concept_id, flush_concepts = concept_interner(facts_conn)
        known = {
            row["symbol"]: row["cik"]
            for row in conn.execute("SELECT symbol, cik FROM companies").fetchall()
        }
        with httpx.Client(
            timeout=60.0,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        ) as client:
            ticker_cik: dict[str, str] | None = None
            for symbol in symbols:
                sym = symbol.strip().upper()
                cik = known.get(sym)
                if not cik:
                    if ticker_cik is None:
                        ticker_cik = _load_ticker_cik_map(client)
                        time.sleep(_REQUEST_INTERVAL_SEC)
                    cik = ticker_cik.get(_base_ticker(sym))
                if not cik:
                    logger.debug("No CIK for %s", sym)
                    written[sym] = 0
                    continue
                try:
                    if sym not in known:
                        # Record symbol -> CIK (with sector) or nothing can resolve
                        # this symbol later — the reader looks up `companies` first.
                        sic, sic_desc = _fetch_submissions(client, cik)
                        time.sleep(_REQUEST_INTERVAL_SEC)
                        _upsert_company(conn, symbol=sym, cik=cik, sic=sic, sic_desc=sic_desc)
                        conn.commit()
                        known[sym] = cik
                    payload = _fetch_company_facts(client, cik)
                    time.sleep(_REQUEST_INTERVAL_SEC)
                    rows = fact_rows(payload, concept_id) if payload else []
                    flush_concepts()
                    upsert_facts(facts_conn, rows)
                    facts_conn.commit()
                    written[sym] = len(rows)
                    logger.info("Cached %d facts for %s (CIK %s)", len(rows), sym, cik)
                except Exception as exc:  # noqa: BLE001 - one bad symbol must not kill the run
                    logger.warning("  %s (CIK %s): companyfacts failed (%s)", sym, cik, str(exc)[:80])
                    written[sym] = 0
    finally:
        conn.close()
        facts_conn.close()
    return written


def _facts_rows(
    cik: str,
    *,
    facts_db: Path,
    tags: tuple[str, ...] | None = None,
    as_of: str | None = None,
    limit: int | None = None,
):
    """Point-in-time fact rows for one filer, newest period first.

    The ordering is the dedup rule for every caller: newest period, then
    FY-labelled, then newest filing. The FY preference matters for
    balance-sheet instants — a later 10-Q repeats the fiscal year-end balance
    as a `Q1` comparative, and taking that copy (same value, wrong label)
    leaves the annual series stranded a year back.
    """
    conn = connect(facts_db)
    try:
        ensure_bulk_schema(conn)
        where = ["f.cik = ?"]
        params: list = [int(cik)]
        if tags:
            # Resolve tag names to ids first and filter on `concept_id`: that is
            # the second column of the primary key, so it seeks (cik, concept_id)
            # directly instead of scanning every fact the filer ever reported and
            # joining each one to test its name.
            ids = [
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM concepts WHERE tag IN ({','.join('?' * len(tags))})",
                    tags,
                )
            ]
            if not ids:
                return []
            where.append(f"f.concept_id IN ({','.join('?' * len(ids))})")
            params.extend(ids)
        if as_of:
            where.append("f.filed <= ?")
            params.append(as_of)
        sql = f"""
            SELECT k.tag, k.taxonomy, k.label, f.unit, f.period_start, f.period_end,
                   f.fy, f.fp, f.form, f.filed, f.val
            FROM facts f JOIN concepts k ON k.id = f.concept_id
            WHERE {' AND '.join(where)}
            ORDER BY f.period_end DESC,
                     CASE WHEN UPPER(COALESCE(f.fp, '')) = 'FY' THEN 0 ELSE 1 END ASC,
                     f.filed DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def latest_financials(
    symbol: str,
    *,
    as_of: str | None = None,
    periods: int = 4,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    facts_db: Path = DEFAULT_FACTS_DB_PATH,
) -> dict | None:
    """Recent annual and quarterly statement values per metric, point-in-time.

    `as_of` (YYYY-MM-DD) restricts to what had actually been FILED by that date,
    so a backtest sees what the market saw. Within a period, the latest filing
    wins — a restatement supersedes the original rather than duplicating it.
    Returns None when the symbol has no cached company row.

    This is the twelve-metric convenience view over the full corpus. Anything
    outside it is reachable by name through `concept_series`.
    """
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        company = _company_row(conn, symbol)
    finally:
        conn.close()
    if company is None:
        return None

    tag_to_metric = {
        tag: metric for metric, tags in FINANCIAL_CONCEPTS.items() for tag in tags
    }
    rows = _facts_rows(
        company["cik"],
        facts_db=facts_db,
        tags=tuple(tag_to_metric),
        as_of=as_of,
    )

    metrics: dict[str, dict] = {}
    seen: set[tuple] = set()
    for row in rows:
        metric = tag_to_metric[row["tag"]]
        period = (metric, row["period_start"], row["period_end"])
        if period in seen:
            continue  # an earlier filing of a period we already took
        seen.add(period)
        bucket = _period_bucket(row["period_start"], row["period_end"], row["fp"])
        if bucket is None:
            continue  # a 6- or 9-month year-to-date figure — not a comparable series
        entry = metrics.setdefault(
            metric, {"unit": row["unit"], "annual": [], "quarterly": []}
        )
        if row["unit"] != entry["unit"]:
            continue  # a second reporting currency — never mix it into one series
        if len(entry[bucket]) >= periods:
            continue
        entry[bucket].append(
            {
                "period_end": row["period_end"],
                "period_start": row["period_start"] or None,
                "val": row["val"],
                "tag": row["tag"],
                "fy": row["fy"],
                "fp": row["fp"],
                "form": row["form"],
                "filed": row["filed"],
            }
        )

    _flag_stale_metrics(metrics, rows)
    return {
        "symbol": company["symbol"],
        "cik": company["cik"],
        "sector": company["sector"],
        "sic_desc": company["sic_desc"],
        "as_of": as_of,
        "metrics": metrics,
    }


# A concept the filer abandoned still has a newest row, and served without this
# it reads as current: JPM stopped tagging quarterly `Revenues` in 2014 (banks
# report NoninterestIncome + InterestIncomeExpenseNet instead), so "JPM latest
# quarterly revenue" answered $24.2B from 2014-09-30. We do not invent a
# replacement — summing two tags would be us computing a figure and labelling it
# as-reported — we mark the series stale and let the caller find the right
# concept via `company_concepts`.
#
# Per BUCKET, not per metric: JPM's ANNUAL revenue is current (filed 2026-02-13)
# while its quarterly series died in 2014, so a metric-level flag hides exactly
# the case that motivated this.
_STALE_AFTER_DAYS = 400


def _flag_stale_metrics(metrics: dict[str, dict], rows) -> None:
    if not rows:
        return
    try:
        cutoff = (
            _date.fromisoformat(max(row["filed"] for row in rows)[:10]).toordinal()
            - _STALE_AFTER_DAYS
        )
    except ValueError:
        return
    for entry in metrics.values():
        stale = []
        for bucket in ("annual", "quarterly"):
            if not entry[bucket]:
                continue
            last = max(item["filed"] for item in entry[bucket])
            try:
                if _date.fromisoformat(last[:10]).toordinal() < cutoff:
                    stale.append(bucket)
                    entry[f"{bucket}_last_filed"] = last
            except ValueError:
                continue
        if stale:
            entry["stale"] = stale


def concept_series(
    symbol: str,
    concept: str,
    *,
    as_of: str | None = None,
    periods: int = 8,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    facts_db: Path = DEFAULT_FACTS_DB_PATH,
) -> dict | None:
    """Point-in-time series for ANY XBRL concept the filer reports, by tag name.

    The twelve canonical metrics are a convenience, not the boundary: a filer
    reports ~250 concepts and the interesting question is usually about one of
    the other 240 (lease obligations, buybacks, deferred revenue, headcount).
    Returns None when the symbol is unresolved.
    """
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        company = _company_row(conn, symbol)
    finally:
        conn.close()
    if company is None:
        return None

    rows = _facts_rows(
        company["cik"], facts_db=facts_db, tags=(concept.strip(),), as_of=as_of
    )
    annual: list[dict] = []
    quarterly: list[dict] = []
    seen: set[tuple] = set()
    unit = None
    label = None
    for row in rows:
        key = (row["period_start"], row["period_end"])
        if key in seen:
            continue
        seen.add(key)
        if unit is None:
            unit, label = row["unit"], row["label"]
        if row["unit"] != unit:
            continue
        # Balance-sheet instants have no start and fall to their fiscal-period
        # label; 6-/9-month year-to-date durations fit no comparable series.
        bucket = _period_bucket(row["period_start"], row["period_end"], row["fp"])
        target = {"annual": annual, "quarterly": quarterly}.get(bucket)
        if target is None or len(target) >= periods:
            continue
        target.append(
            {
                "period_end": row["period_end"],
                "period_start": row["period_start"] or None,
                "val": row["val"],
                "fy": row["fy"],
                "fp": row["fp"],
                "form": row["form"],
                "filed": row["filed"],
            }
        )
    return {
        "symbol": company["symbol"],
        "cik": company["cik"],
        "concept": concept.strip(),
        "label": label,
        "unit": unit,
        "as_of": as_of,
        "annual": annual,
        "quarterly": quarterly,
    }


def search_concepts(
    text: str,
    *,
    limit: int = 25,
    facts_db: Path = DEFAULT_FACTS_DB_PATH,
) -> list[dict]:
    """Find XBRL concepts by tag, human label, or definition text.

    An agent knows "research and development", not
    `ResearchAndDevelopmentExpense` — without this the corpus is only reachable
    by callers who already know the tag names.
    """
    needle = f"%{text.strip()}%"
    conn = connect(facts_db)
    try:
        ensure_bulk_schema(conn)
        rows = conn.execute(
            """
            SELECT taxonomy, tag, label, description FROM concepts
            WHERE tag LIKE ? OR label LIKE ? OR description LIKE ?
            ORDER BY CASE WHEN tag LIKE ? THEN 0 ELSE 1 END, length(tag) ASC
            LIMIT ?
            """,
            (needle, needle, needle, needle, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "concept": row["tag"],
            "taxonomy": row["taxonomy"],
            "label": row["label"],
            "description": (row["description"] or "")[:300] or None,
        }
        for row in rows
    ]


def company_concepts(
    symbol: str,
    *,
    limit: int = 400,
    cache_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    facts_db: Path = DEFAULT_FACTS_DB_PATH,
) -> list[dict] | None:
    """Every concept this filer actually reports, most-recently-filed first.

    Coverage varies by filer (AMZN tags no total `Liabilities`), so "what can I
    even ask about this company" has to be answerable before asking.
    """
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        company = _company_row(conn, symbol)
    finally:
        conn.close()
    if company is None:
        return None

    conn = connect(facts_db)
    try:
        ensure_bulk_schema(conn)
        rows = conn.execute(
            """
            SELECT k.tag, k.taxonomy, k.label, COUNT(*) AS n, MAX(f.filed) AS last_filed
            FROM facts f JOIN concepts k ON k.id = f.concept_id
            WHERE f.cik = ?
            GROUP BY f.concept_id
            ORDER BY last_filed DESC, n DESC
            LIMIT ?
            """,
            (int(company["cik"]), int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "concept": row["tag"],
            "taxonomy": row["taxonomy"],
            "label": row["label"],
            "facts": row["n"],
            "last_filed": row["last_filed"],
        }
        for row in rows
    ]


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
    parser.add_argument(
        "--build-financials",
        metavar="SYMBOLS",
        help="Fetch EDGAR companyfacts statements for a comma-separated symbol list",
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

    if args.build_financials:
        symbols = [s for s in (t.strip() for t in args.build_financials.split(",")) if s]
        written = build_financials(symbols, cache_db=args.cache_db)
        total = sum(written.values())
        empty = sorted(s for s, n in written.items() if n == 0)
        print(f"financials: {total:,} rows across {len(written) - len(empty)}/{len(written)} symbols")
        if empty:
            print(f"no facts for: {', '.join(empty)}")

    if args.lookup:
        _lookup_symbol(args.lookup, cache_db=args.cache_db)

    if args.coverage:
        report = coverage_report(cache_db=args.cache_db, bars_db=args.bars_db)
        _print_coverage(report)

    if not (args.build or args.build_financials or args.lookup or args.coverage):
        _parse_args(["--help"])


if __name__ == "__main__":
    main()
