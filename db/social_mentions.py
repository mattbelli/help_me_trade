"""Per-symbol social mention counts — hourly ET buckets + baseline math.

Buckets use America/New_York hour labels (``YYYY-MM-DDTHH``) so "10x normal for
this time of day" matches how traders think about the session, not UTC midnight.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from trading.market_hours import _ET

SOURCES = frozenset({"reddit", "stocktwits"})
_BASELINE_LOOKBACK_DAYS = 14
_MIN_BASELINE_DAYS = 3


def et_hour_bucket(when: datetime | None = None) -> str:
    """Return the ET hour bucket label for ``when`` (default: now)."""
    t = when.astimezone(_ET) if when is not None else datetime.now(_ET)
    return t.strftime("%Y-%m-%dT%H")


def init_social_mentions_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS social_mentions (
                symbol TEXT NOT NULL,
                bucket TEXT NOT NULL,
                source TEXT NOT NULL,
                mentions INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, bucket, source)
            );

            CREATE INDEX IF NOT EXISTS idx_social_mentions_bucket
                ON social_mentions(bucket);
            CREATE INDEX IF NOT EXISTS idx_social_mentions_symbol_bucket
                ON social_mentions(symbol, bucket);
            """
        )
        conn.commit()


def record_mentions(
    counts: dict[str, dict[str, int]],
    *,
    bucket: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Additive upsert of ``counts[symbol][source] = delta``.

    Returns the number of rows touched.
    """
    init_social_mentions_tables(db_path)
    if not counts:
        return 0

    bucket = bucket or et_hour_bucket()
    touched = 0
    with _connect(db_path) as conn:
        for symbol, by_source in counts.items():
            sym = str(symbol).strip().upper()
            if not sym or not isinstance(by_source, dict):
                continue
            for source, delta in by_source.items():
                src = str(source).strip().lower()
                if src not in SOURCES:
                    continue
                try:
                    amount = int(delta)
                except (TypeError, ValueError):
                    continue
                if amount <= 0:
                    continue
                conn.execute(
                    """
                    INSERT INTO social_mentions (symbol, bucket, source, mentions)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol, bucket, source) DO UPDATE SET
                        mentions = mentions + excluded.mentions
                    """,
                    (sym, bucket, src, amount),
                )
                touched += 1
        conn.commit()
    return touched


def _sum_mentions_for_bucket(
    conn: Any,
    *,
    symbol: str,
    bucket: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(mentions), 0) AS total
        FROM social_mentions
        WHERE symbol = ? AND bucket = ?
        """,
        (symbol, bucket),
    ).fetchone()
    return int(row["total"]) if row else 0


def _today_et_prefix(when: datetime | None = None) -> str:
    t = when.astimezone(_ET) if when is not None else datetime.now(_ET)
    return t.strftime("%Y-%m-%d")


def _hour_suffix(bucket: str) -> str:
    if "T" not in bucket:
        return bucket
    return bucket.split("T", 1)[1]


def _day_part_hours(hour_suffix: str) -> tuple[str, tuple[str, ...]]:
    """Map an ET hour to the stable v1 social-baseline day parts."""
    hour = int(hour_suffix)
    if 4 <= hour <= 8:
        name, hours = "pre_open", range(4, 9)
    elif 9 <= hour <= 11:
        name, hours = "open", range(9, 12)
    elif 12 <= hour <= 13:
        name, hours = "midday", range(12, 14)
    elif 14 <= hour <= 15:
        name, hours = "close", range(14, 16)
    else:
        name, hours = "after_hours", (*range(16, 24), *range(0, 4))
    return name, tuple(f"{value:02d}" for value in hours)


def _baseline_hour_mean(
    conn: Any,
    *,
    symbol: str,
    hour_suffix: str,
    exclude_date_prefix: str,
    lookback_days: int = _BASELINE_LOOKBACK_DAYS,
    source: str | None = None,
) -> tuple[float | None, int]:
    """Mean hourly mentions in this day-part over trailing days.

    Returns (mean, day_count). Mean is None when fewer than ``_MIN_BASELINE_DAYS``
    distinct days have observations.
    """
    end_day = datetime.strptime(exclude_date_prefix, "%Y-%m-%d").date() - timedelta(days=1)
    start_day = end_day - timedelta(days=lookback_days - 1)
    _, hours = _day_part_hours(hour_suffix)
    placeholders = ",".join("?" for _ in hours)
    source_clause = " AND source = ?" if source is not None else ""
    params: list[Any] = [
        symbol,
        f"{start_day.isoformat()}T00",
        f"{end_day.isoformat()}T23",
        *hours,
    ]
    if source is not None:
        params.append(source)
    rows = conn.execute(
        f"""
        SELECT bucket, SUM(mentions) AS total
        FROM social_mentions
        WHERE symbol = ?
          AND bucket >= ?
          AND bucket <= ?
          AND substr(bucket, 12, 2) IN ({placeholders})
          {source_clause}
        GROUP BY bucket
        """,
        params,
    ).fetchall()

    samples: list[int] = []
    days: set[str] = set()
    for row in rows:
        bucket = str(row["bucket"])
        day_prefix = bucket.split("T", 1)[0]
        if day_prefix == exclude_date_prefix:
            continue
        days.add(day_prefix)
        samples.append(int(row["total"]))

    day_count = len(days)
    if day_count < _MIN_BASELINE_DAYS:
        return None, day_count
    return sum(samples) / len(samples), day_count


def get_source_buzz(
    symbol: str,
    source: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return day-part-normalized buzz for one social venue."""
    init_social_mentions_tables(db_path)
    sym = str(symbol).strip().upper()
    src = str(source).strip().lower()
    if src not in SOURCES:
        raise ValueError(f"unsupported social source: {source}")
    when = now.astimezone(_ET) if now is not None else datetime.now(_ET)
    bucket = et_hour_bucket(when)
    today_prefix = _today_et_prefix(when)
    hour_suffix = _hour_suffix(bucket)
    day_part, _ = _day_part_hours(hour_suffix)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(mentions), 0) AS total
            FROM social_mentions
            WHERE symbol = ? AND bucket = ? AND source = ?
            """,
            (sym, bucket, src),
        ).fetchone()
        baseline, baseline_days = _baseline_hour_mean(
            conn,
            symbol=sym,
            hour_suffix=hour_suffix,
            exclude_date_prefix=today_prefix,
            source=src,
        )
    last_hour = int(row["total"]) if row else 0
    ratio = last_hour / baseline if baseline is not None and baseline > 0 else None
    return {
        "symbol": sym,
        "source": src,
        "last_hour": last_hour,
        "baseline_hour": baseline,
        "baseline_days": baseline_days,
        "anomaly_ratio": ratio,
        "state": "warming" if baseline is None else "ready",
        "bucket": bucket,
        "day_part": day_part,
    }


def get_buzz(
    symbol: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return mention stats + honest baseline for one symbol."""
    init_social_mentions_tables(db_path)
    sym = str(symbol).strip().upper()
    when = now.astimezone(_ET) if now is not None else datetime.now(_ET)
    current_bucket = et_hour_bucket(when)
    today_prefix = _today_et_prefix(when)
    hour_suffix = _hour_suffix(current_bucket)
    day_part, _ = _day_part_hours(hour_suffix)

    with _connect(db_path) as conn:
        last_hour = _sum_mentions_for_bucket(conn, symbol=sym, bucket=current_bucket)
        row = conn.execute(
            """
            SELECT COALESCE(SUM(mentions), 0) AS total
            FROM social_mentions
            WHERE symbol = ? AND bucket LIKE ?
            """,
            (sym, f"{today_prefix}T%"),
        ).fetchone()
        today_total = int(row["total"]) if row else 0
        baseline_hour, baseline_days = _baseline_hour_mean(
            conn,
            symbol=sym,
            hour_suffix=hour_suffix,
            exclude_date_prefix=today_prefix,
        )

    state = "warming" if baseline_hour is None else "ready"
    anomaly_ratio: float | None = None
    if baseline_hour is not None and baseline_hour > 0:
        anomaly_ratio = last_hour / baseline_hour

    return {
        "symbol": sym,
        "last_hour": last_hour,
        "today_total": today_total,
        "baseline_hour": baseline_hour,
        "baseline_days": baseline_days,
        "anomaly_ratio": anomaly_ratio,
        "state": state,
        "bucket": current_bucket,
        "day_part": day_part,
    }


def get_trending(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 20,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Symbols ranked by today's ET mention total with buzz stats."""
    init_social_mentions_tables(db_path)
    when = now.astimezone(_ET) if now is not None else datetime.now(_ET)
    today_prefix = _today_et_prefix(when)
    cap = max(1, min(int(limit), 100))

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT symbol, SUM(mentions) AS today_total
            FROM social_mentions
            WHERE bucket LIKE ?
            GROUP BY symbol
            HAVING today_total > 0
            ORDER BY today_total DESC
            LIMIT ?
            """,
            (f"{today_prefix}T%", cap),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        sym = str(row["symbol"])
        buzz = get_buzz(sym, db_path=db_path, now=when)
        out.append(
            {
                "symbol": sym,
                "today_total": int(row["today_total"]),
                "last_hour": buzz["last_hour"],
                "baseline_hour": buzz["baseline_hour"],
                "anomaly_ratio": buzz["anomaly_ratio"],
                "state": buzz["state"],
            }
        )
    return out
