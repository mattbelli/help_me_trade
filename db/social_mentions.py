"""Per-symbol social mention counts — hourly ET buckets + baseline math.

Buckets use America/New_York hour labels (``YYYY-MM-DDTHH``) so "10x normal for
this time of day" matches how traders think about the session, not UTC midnight.
Also persists Reddit posts (title-level) for published-hour rollups + overlap.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect
from trading.market_hours import _ET

SOURCES = frozenset({"reddit", "stocktwits"})
_BASELINE_LOOKBACK_DAYS = 14
_MIN_BASELINE_DAYS = 3
_OVERLAP_CAP = 20


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

            CREATE TABLE IF NOT EXISTS social_posts (
                post_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                subreddit TEXT,
                author TEXT,
                published_at TEXT NOT NULL,
                bucket TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                url TEXT,
                n_symbols INTEGER NOT NULL,
                has_url INTEGER NOT NULL DEFAULT 0,
                word_count INTEGER NOT NULL DEFAULT 0,
                dd_flag INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_social_posts_author
                ON social_posts(author);
            CREATE INDEX IF NOT EXISTS idx_social_posts_bucket
                ON social_posts(bucket);
            CREATE INDEX IF NOT EXISTS idx_social_posts_published
                ON social_posts(published_at);

            CREATE TABLE IF NOT EXISTS social_post_symbols (
                post_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                PRIMARY KEY (post_id, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_social_post_symbols_symbol
                ON social_post_symbols(symbol);
            """
        )
        conn.commit()


def record_reddit_posts(
    posts: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """INSERT OR IGNORE Reddit posts + symbol links. Returns newly inserted rows.

    Existing ``post_id`` rows are skipped entirely (durable dedupe). Each returned
    dict keeps the caller's ``symbols`` set/list for mention rollup.
    """
    init_social_mentions_tables(db_path)
    if not posts:
        return []

    fetched_at = now_iso()
    newly: list[dict[str, Any]] = []
    with _connect(db_path) as conn:
        for post in posts:
            post_id = str(post.get("post_id") or post.get("id") or "").strip()
            if not post_id:
                continue
            symbols_raw = post.get("symbols") or []
            symbols = sorted(
                {
                    str(s).strip().upper()
                    for s in symbols_raw
                    if isinstance(s, str) and str(s).strip()
                }
            )
            author_raw = post.get("author")
            author = (
                str(author_raw).strip()
                if author_raw is not None and str(author_raw).strip()
                else None
            )
            published_at = str(post.get("published_at") or "").strip() or fetched_at
            bucket = str(post.get("bucket") or "").strip() or et_hour_bucket()
            title = str(post.get("title") or "")
            url = post.get("url")
            url_s = str(url).strip() if url is not None and str(url).strip() else None
            subreddit = post.get("subreddit")
            sub_s = (
                str(subreddit).strip()
                if subreddit is not None and str(subreddit).strip()
                else None
            )
            n_symbols = int(post.get("n_symbols") if "n_symbols" in post else len(symbols))
            has_url = 1 if int(post.get("has_url") or 0) else 0
            word_count = max(0, int(post.get("word_count") or 0))
            dd_flag = 1 if int(post.get("dd_flag") or 0) else 0

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO social_posts (
                    post_id, source, subreddit, author, published_at, bucket,
                    title, url, n_symbols, has_url, word_count, dd_flag, fetched_at
                ) VALUES (?, 'reddit', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    sub_s,
                    author,
                    published_at,
                    bucket,
                    title,
                    url_s,
                    n_symbols,
                    has_url,
                    word_count,
                    dd_flag,
                    fetched_at,
                ),
            )
            if cur.rowcount != 1:
                continue
            for sym in symbols:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO social_post_symbols (post_id, symbol)
                    VALUES (?, ?)
                    """,
                    (post_id, sym),
                )
            newly.append(
                {
                    **post,
                    "post_id": post_id,
                    "author": author,
                    "published_at": published_at,
                    "bucket": bucket,
                    "title": title,
                    "url": url_s,
                    "subreddit": sub_s,
                    "n_symbols": n_symbols,
                    "has_url": has_url,
                    "word_count": word_count,
                    "dd_flag": dd_flag,
                    "symbols": symbols,
                    "fetched_at": fetched_at,
                }
            )
        conn.commit()
    return newly


def recent_posts_for_symbol(
    symbol: str,
    *,
    limit: int = 20,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Recent Reddit post previews for a symbol (no bodies)."""
    init_social_mentions_tables(db_path)
    sym = str(symbol).strip().upper()
    if not sym:
        return []
    cap = max(1, min(int(limit), 20))
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.title, p.author, p.subreddit, p.published_at,
                   p.n_symbols, p.dd_flag, p.has_url, p.post_id, p.url
            FROM social_posts p
            JOIN social_post_symbols s ON s.post_id = p.post_id
            WHERE s.symbol = ?
            ORDER BY p.published_at DESC
            LIMIT ?
            """,
            (sym, cap),
        ).fetchall()
    return [
        {
            "post_id": str(r["post_id"]),
            "title": str(r["title"] or ""),
            "author": r["author"],
            "subreddit": r["subreddit"],
            "published_at": str(r["published_at"]),
            "n_symbols": int(r["n_symbols"]),
            "dd_flag": int(r["dd_flag"]),
            "has_url": int(r["has_url"]),
            "url": r["url"],
        }
        for r in rows
    ]


def user_overlap_neighbors(
    symbol: str,
    *,
    lookback_days: int = 30,
    min_frac: float = 0.20,
    min_authors: int = 5,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Oxford-style author overlap on single-ticker posts (perception only).

    Link A–B when ≥``min_frac`` of authors who single-ticker-posted A also
    single-ticker-posted B. Gate on ``min_authors`` distinct authors for A —
    do not invent Oxford's 150-mention floor.
    """
    init_social_mentions_tables(db_path)
    sym = str(symbol).strip().upper()
    if not sym:
        return []
    lookback = max(1, int(lookback_days))
    frac_floor = float(min_frac)
    author_floor = max(1, int(min_authors))
    # RSS stamps are UTC (`...+00:00`). Compare in the same form so lookback
    # is not scrambled by ET-offset vs UTC lexicographic order.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    with _connect(db_path) as conn:
        author_rows = conn.execute(
            """
            SELECT DISTINCT p.author AS author
            FROM social_posts p
            JOIN social_post_symbols s ON s.post_id = p.post_id
            WHERE s.symbol = ?
              AND p.n_symbols = 1
              AND p.author IS NOT NULL
              AND p.published_at >= ?
            """,
            (sym, cutoff),
        ).fetchall()
        authors_of_query = len(author_rows)
        if authors_of_query < author_floor:
            return []

        rows = conn.execute(
            """
            WITH query_authors AS (
                SELECT DISTINCT p.author AS author
                FROM social_posts p
                JOIN social_post_symbols s ON s.post_id = p.post_id
                WHERE s.symbol = ?
                  AND p.n_symbols = 1
                  AND p.author IS NOT NULL
                  AND p.published_at >= ?
            )
            SELECT
                s2.symbol AS symbol,
                COUNT(DISTINCT p2.author) AS shared_authors
            FROM social_posts p2
            JOIN social_post_symbols s2 ON s2.post_id = p2.post_id
            JOIN query_authors qa ON qa.author = p2.author
            WHERE p2.n_symbols = 1
              AND p2.author IS NOT NULL
              AND p2.published_at >= ?
              AND s2.symbol != ?
            GROUP BY s2.symbol
            HAVING shared_authors >= 2
            """,
            (sym, cutoff, cutoff, sym),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        shared = int(row["shared_authors"])
        frac = shared / authors_of_query
        if frac < frac_floor:
            continue
        out.append(
            {
                "symbol": str(row["symbol"]),
                "shared_authors": shared,
                "authors_of_query": authors_of_query,
                "frac": frac,
            }
        )
    out.sort(key=lambda r: (-float(r["frac"]), str(r["symbol"])))
    return out[:_OVERLAP_CAP]


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
