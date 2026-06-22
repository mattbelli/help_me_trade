"""Market-regime layer — FRED macro cache, breadth from cached bars, point-in-time accessor."""

from __future__ import annotations

import argparse
import csv
import io
import logging
from collections import defaultdict
from pathlib import Path

import numpy as np

from db.base import connect
from research.bars import DEFAULT_BARS_DB_PATH, RESEARCH_DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_REGIME_DB_PATH = RESEARCH_DATA_DIR / "regime.db"

FRED_SERIES = {
    "vix": "VIXCLS",
    "y10": "DGS10",
    "y2": "DGS2",
    "hy_spread": "BAMLH0A0HYM2",
}

# cosd (custom observation start) forces FULL history — without it the keyless CSV
# endpoint returns a truncated recent window for some series (e.g. HY credit spread).
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd=1990-01-01"

_CREATE_MACRO = """
CREATE TABLE IF NOT EXISTS macro (
    series TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (series, date)
)
"""

_CREATE_BREADTH = """
CREATE TABLE IF NOT EXISTS breadth (
    date TEXT NOT NULL PRIMARY KEY,
    pct_above_200 REAL NOT NULL,
    pct_above_50 REAL NOT NULL,
    adv_dec REAL NOT NULL
)
"""

_VIX_TRAILING_OBS = 252


def _ensure_schema(conn) -> None:
    conn.execute(_CREATE_MACRO)
    conn.execute(_CREATE_BREADTH)
    conn.commit()


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Trailing SMA; NaN until `window` observations are available (point-in-time)."""
    out = np.full(values.shape, np.nan, dtype=float)
    n = len(values)
    if n < window:
        return out
    cs = np.concatenate([[0.0], np.cumsum(values, dtype=float)])
    out[window - 1 :] = (cs[window:] - cs[:-window]) / window
    return out


def _parse_fred_csv(text: str, series_id: str) -> list[tuple[str, float]]:
    """Parse FRED graph CSV into (date, value) rows; skip '.' missing values."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    rows = list(reader)
    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    if "observation_date" in header:
        date_idx = header.index("observation_date")
    elif "date" in header:
        date_idx = header.index("date")
    else:
        date_idx = 0

    value_idx = None
    for idx, name in enumerate(header):
        if name == series_id.lower() or (value_idx is None and idx != date_idx):
            value_idx = idx
            break
    if value_idx is None:
        return []

    parsed: list[tuple[str, float]] = []
    for row in rows[1:]:
        if len(row) <= max(date_idx, value_idx):
            continue
        raw_date = row[date_idx].strip()
        raw_value = row[value_idx].strip()
        if not raw_date or raw_value in {"", "."}:
            continue
        parsed.append((raw_date, float(raw_value)))
    return parsed


def fetch_fred(
    series_ids: dict[str, str],
    *,
    cache_db: Path = DEFAULT_REGIME_DB_PATH,
    user_agent: str = "help_me_trade-research matt.belliveau@gmail.com",
) -> dict[str, int]:
    """Fetch FRED series via keyless CSV endpoint and upsert into macro cache."""
    import httpx

    counts: dict[str, int] = {}
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        with httpx.Client(
            timeout=60.0,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        ) as client:
            for series_key, series_id in series_ids.items():
                url = FRED_CSV_URL.format(series_id=series_id)
                response = client.get(url)
                response.raise_for_status()
                rows = _parse_fred_csv(response.text, series_id)
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO macro (series, date, value)
                    VALUES (?, ?, ?)
                    """,
                    [(series_key, d, v) for d, v in rows],
                )
                counts[series_key] = len(rows)
        conn.commit()
    finally:
        conn.close()
    return counts


def compute_breadth(
    *,
    source: str = "norgate",
    bars_db: Path = DEFAULT_BARS_DB_PATH,
    cache_db: Path = DEFAULT_REGIME_DB_PATH,
) -> int:
    """Compute point-in-time breadth metrics from cached daily bars."""
    bars_conn = connect(bars_db)
    try:
        bar_rows = bars_conn.execute(
            """
            SELECT symbol, date, c
            FROM daily_bars
            WHERE source = ?
            ORDER BY symbol ASC, date ASC
            """,
            (source,),
        ).fetchall()
        trading_dates = [
            row["date"]
            for row in bars_conn.execute(
                """
                SELECT DISTINCT date
                FROM daily_bars
                WHERE source = ?
                ORDER BY date ASC
                """,
                (source,),
            ).fetchall()
        ]
    finally:
        bars_conn.close()

    if not bar_rows:
        return 0

    by_symbol: dict[str, tuple[list[str], np.ndarray]] = {}
    current_symbol: str | None = None
    dates: list[str] = []
    closes: list[float] = []
    for row in bar_rows:
        sym = row["symbol"]
        if sym != current_symbol:
            if current_symbol is not None:
                by_symbol[current_symbol] = (dates, np.asarray(closes, dtype=float))
            current_symbol = sym
            dates = []
            closes = []
        dates.append(row["date"])
        closes.append(float(row["c"]))
    if current_symbol is not None:
        by_symbol[current_symbol] = (dates, np.asarray(closes, dtype=float))

    eligible_200: dict[str, int] = defaultdict(int)
    above_200: dict[str, int] = defaultdict(int)
    eligible_50: dict[str, int] = defaultdict(int)
    above_50: dict[str, int] = defaultdict(int)
    traded: dict[str, int] = defaultdict(int)
    advancers: dict[str, int] = defaultdict(int)
    decliners: dict[str, int] = defaultdict(int)

    for sym_dates, sym_closes in by_symbol.values():
        sma200 = _rolling_mean(sym_closes, 200)
        sma50 = _rolling_mean(sym_closes, 50)
        for i, d in enumerate(sym_dates):
            if i >= 199 and not np.isnan(sma200[i]):
                eligible_200[d] += 1
                if sym_closes[i] > sma200[i]:
                    above_200[d] += 1
            if i >= 49 and not np.isnan(sma50[i]):
                eligible_50[d] += 1
                if sym_closes[i] > sma50[i]:
                    above_50[d] += 1
            if i >= 1:
                traded[d] += 1
                if sym_closes[i] > sym_closes[i - 1]:
                    advancers[d] += 1
                elif sym_closes[i] < sym_closes[i - 1]:
                    decliners[d] += 1

    breadth_rows: list[tuple[str, float, float, float]] = []
    for d in trading_dates:
        pct200 = above_200[d] / eligible_200[d] if eligible_200[d] else 0.0
        pct50 = above_50[d] / eligible_50[d] if eligible_50[d] else 0.0
        if traded[d]:
            adv_dec = (advancers[d] - decliners[d]) / traded[d]
        else:
            adv_dec = 0.0
        breadth_rows.append((d, pct200, pct50, adv_dec))

    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        conn.executemany(
            """
            INSERT OR REPLACE INTO breadth (date, pct_above_200, pct_above_50, adv_dec)
            VALUES (?, ?, ?, ?)
            """,
            breadth_rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(breadth_rows)


def _macro_asof(conn, series: str, d: str) -> float | None:
    row = conn.execute(
        """
        SELECT value FROM macro
        WHERE series = ? AND date <= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (series, d),
    ).fetchone()
    return float(row["value"]) if row else None


def _vix_percentile_1y(conn, d: str, current_vix: float | None) -> float | None:
    if current_vix is None:
        return None
    rows = conn.execute(
        """
        SELECT value FROM macro
        WHERE series = 'vix' AND date <= ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (d, _VIX_TRAILING_OBS),
    ).fetchall()
    if not rows:
        return None
    values = np.asarray([float(row["value"]) for row in rows], dtype=float)
    return float(np.sum(values <= current_vix) / len(values) * 100.0)


def market_regime(d: str, *, cache_db: Path = DEFAULT_REGIME_DB_PATH) -> dict | None:
    """Return point-in-time regime snapshot as of date `d`."""
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)

        vix = _macro_asof(conn, "vix", d)
        y10 = _macro_asof(conn, "y10", d)
        y2 = _macro_asof(conn, "y2", d)
        hy_spread = _macro_asof(conn, "hy_spread", d)
        term_spread = (y10 - y2) if y10 is not None and y2 is not None else None

        breadth_row = conn.execute(
            "SELECT pct_above_200, pct_above_50, adv_dec FROM breadth WHERE date = ?",
            (d,),
        ).fetchone()

        return {
            "vix": vix,
            "vix_pctile_1y": _vix_percentile_1y(conn, d, vix),
            "y10": y10,
            "y2": y2,
            "term_spread": term_spread,
            "hy_spread": hy_spread,
            "breadth_above_200": float(breadth_row["pct_above_200"]) if breadth_row else None,
            "breadth_above_50": float(breadth_row["pct_above_50"]) if breadth_row else None,
            "adv_dec": float(breadth_row["adv_dec"]) if breadth_row else None,
        }
    finally:
        conn.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Market-regime macro cache and breadth")
    parser.add_argument("--refresh-macro", action="store_true", help="Fetch FRED series into cache")
    parser.add_argument(
        "--refresh-breadth",
        action="store_true",
        help="Compute breadth from cached daily bars",
    )
    parser.add_argument("--at", metavar="YYYY-MM-DD", help="Print regime snapshot for date")
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_REGIME_DB_PATH,
        help="Regime cache DB path",
    )
    parser.add_argument(
        "--bars-db",
        type=Path,
        default=DEFAULT_BARS_DB_PATH,
        help="Daily bars DB path",
    )
    parser.add_argument("--source", default="norgate", help="Bar source tag for breadth")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    if args.refresh_macro:
        counts = fetch_fred(FRED_SERIES, cache_db=args.cache_db)
        for series_key, n in counts.items():
            print(f"{series_key}: {n} rows upserted")

    if args.refresh_breadth:
        n_dates = compute_breadth(
            source=args.source,
            bars_db=args.bars_db,
            cache_db=args.cache_db,
        )
        print(f"breadth: {n_dates} dates written")

    if args.at:
        snapshot = market_regime(args.at, cache_db=args.cache_db)
        if snapshot is None:
            print("No regime data available.")
        else:
            for key, value in snapshot.items():
                print(f"{key}: {value}")

    if not (args.refresh_macro or args.refresh_breadth or args.at):
        _parse_args(["--help"])


if __name__ == "__main__":
    main()
