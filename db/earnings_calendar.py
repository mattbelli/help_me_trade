"""Live earnings calendar — recent + upcoming earnings dates per symbol.

Populated from Finnhub on a daily schedule (see signals/earnings_refresh.py). The
earnings-drift strategy reads it at scan time: the LAST earnings date (to score the
prior reaction) and the NEXT earnings date (to time the pre-earnings entry/exit).

Separate from the research `earnings.db` (EDGAR 8-K history used for backtests) —
this table is the *live forward-looking* feed the running scan depends on.

Two grains live here:

* CONFIRMED rows (``estimated = 0``) — a vendor calendar date or an EDGAR 8-K
  Item 2.02 that already happened. Every pre-existing reader keeps seeing ONLY
  these: ``next_earnings_date`` / ``last_earnings_date`` / ``earnings_dates_for``
  filter estimates out, so no strategy or exit rule can time a trade off a guess.
* ESTIMATED rows (``estimated = 1``, ``source = 'edgar_estimate'``) — the next
  date PROJECTED from a name's own 8-K filing cadence, written by
  ``tools/earnings_coverage_refresh.py`` for the ~5.9k corpus names no vendor
  calendar covers. Read them through ``next_earnings`` /
  ``next_earnings_with_state``, which hand back the estimated flag alongside the
  date — a consumer that wants a date must also carry that it is a projection.

`earnings_coverage` is the companion per-symbol ledger: it separates "this name
has no earnings because it does not file" (an ETF) from "we have never looked".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from datetime import date as _date
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

logger = logging.getLogger(__name__)

# Per-symbol coverage states. The point of the ledger is that these three are
# NOT the same answer: an ETF with no earnings is a fact, an unfetched name is
# an admission.
STATE_DATED = "dated"  # we can offer a next date (confirmed or estimated)
STATE_NON_FILER = "non_filer"  # EDGAR checked: this name files no 8-K Item 2.02
STATE_UNKNOWN = "unknown"  # never fetched, or fetched without a usable date

ESTIMATE_SOURCE = "edgar_estimate"


def init_earnings_calendar_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS earnings_calendar (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                hour TEXT,
                source TEXT NOT NULL DEFAULT 'finnhub',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            CREATE INDEX IF NOT EXISTS idx_earnings_cal_symbol
                ON earnings_calendar (symbol);
            -- Per-symbol EDGAR coverage ledger: what we know about a name's
            -- filing cadence and, crucially, whether we have LOOKED at all.
            CREATE TABLE IF NOT EXISTS earnings_coverage (
                symbol TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                n_filings INTEGER NOT NULL DEFAULT 0,
                last_filing_date TEXT,
                median_gap_days INTEGER,
                next_estimated_date TEXT,
                checked_at TEXT NOT NULL
            );
            """
        )
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(earnings_calendar)")}
        if "estimated" not in cols:
            # Additive + defaulted: every pre-existing row is a confirmed date.
            conn.execute(
                "ALTER TABLE earnings_calendar "
                "ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()


def upsert_earnings(
    rows: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Insert/refresh (symbol, date) rows. Each row: {symbol, date, hour?, source?}.

    `source` is per-row because one refresh mixes providers (forward dates from a
    vendor calendar, past announce dates from SEC EDGAR). It updates on conflict, so
    a date later confirmed by an authoritative source gets re-tagged.

    Rows are CONFIRMED (`estimated = 0`) unless a row says otherwise; projections
    are written through `set_estimated_next_earnings`, never here.
    """
    init_earnings_calendar_tables(db_path)
    now = _now()
    cleaned: list[tuple[str, str, str | None, str, int, str]] = []
    for r in rows:
        symbol = str(r.get("symbol", "")).strip().upper()
        date = str(r.get("date", "")).strip()
        if not symbol or not date:
            continue
        hour = r.get("hour")
        source = str(r.get("source") or "finnhub").strip() or "finnhub"
        estimated = int(bool(r.get("estimated")))
        cleaned.append((symbol, date, str(hour) if hour else None, source, estimated, now))
    if not cleaned:
        return 0
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO earnings_calendar
                (symbol, date, hour, source, estimated, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                hour = COALESCE(excluded.hour, earnings_calendar.hour),
                source = excluded.source,
                estimated = excluded.estimated,
                fetched_at = excluded.fetched_at
            """,
            cleaned,
        )
        conn.commit()
    return len(cleaned)


def set_estimated_next_earnings(
    symbol: str,
    date: str | None,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Replace a symbol's projected next-earnings row (None clears it).

    One estimate per symbol, always: a projection is a current belief about the
    next event, not history, so the old one is deleted rather than accumulated —
    otherwise a name's stale guesses would pile up in front of its real date.
    """
    init_earnings_calendar_tables(db_path)
    sym = symbol.strip().upper()
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM earnings_calendar WHERE symbol = ? AND estimated = 1",
            (sym,),
        )
        if date:
            conn.execute(
                """
                INSERT INTO earnings_calendar
                    (symbol, date, hour, source, estimated, fetched_at)
                VALUES (?, ?, NULL, ?, 1, ?)
                -- A confirmed row already sitting on that date wins: an estimate
                -- must never downgrade a date somebody actually asserted.
                ON CONFLICT(symbol, date) DO NOTHING
                """,
                (sym, date.strip(), ESTIMATE_SOURCE, _now()),
            )
        conn.commit()


# A past-dated vendor row is only a phantom if the vendor abandoned it well BEFORE
# the event: real dates keep getting restamped up to the event day. 3 days absorbs
# one missed daily refresh + the UTC-stamp-vs-ET-event-date skew, so a real event
# whose refresh hiccuped the day before can never be pruned at its own boundary
# (Alpha Vantage is forward-only and drops a date the moment it becomes today —
# without this margin an AMC report could vanish on its print day and blind the
# exit engine).
_PAST_ABANDON_MARGIN_DAYS = 3

# Blast-radius cap: a normal day withdraws ~1% of in-window vendor rows (first-ever
# run: the accumulated backlog measured 48%). Anything past 60% means the confirmed
# set is broken (truncated vendor response), not that half the calendar moved.
_PRUNE_MAX_FRACTION = 0.6
_PRUNE_SMALL_COUNT = 25  # tiny universes: absolute counts this small are never a wipe


def prune_withdrawn_estimates(
    symbols: list[str],
    confirmed: set[tuple[str, str]],
    *,
    start: str,
    end: str,
    today: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Delete vendor estimate rows that no current fetch asserts anymore.

    Vendors move earnings estimates and silently withdraw stale ones; the
    append-only upsert fossilized withdrawn dates as phantom events (GOOGL kept
    showing a Finnhub 07-28 "next earnings" after the real 07-22 report).
    `confirmed` is every (symbol, date) the CURRENT refresh returned; a non-edgar
    row inside the fetched [start, end] window that isn't in it was withdrawn:
      - future rows: deleted (the vendor stopped listing the estimate);
      - today's rows: never touched (a vendor dropping a same-day date is its
        forward-only window rolling over, and EDGAR's confirmation lands later);
      - past rows: deleted only if last asserted ≥ _PAST_ABANDON_MARGIN_DAYS
        before the event date (abandoned-before-event = phantom; dropped-after
        = a rolling window forgetting real history, which EDGAR owns).

    edgar rows are never touched. Note the source column can't distinguish
    vendors (Alpha Vantage rows default to 'finnhub' in the upsert), so callers
    must build `confirmed` from ALL vendor fetches and skip pruning entirely if
    any configured vendor failed — an empty-but-200 vendor response must count
    as a failure, or a broken feed reads as everything-withdrawn.
    """
    init_earnings_calendar_tables(db_path)
    universe = {s.strip().upper() for s in symbols if s and s.strip()}
    if not universe:
        return 0
    confirmed_norm = {
        (str(s).strip().upper(), str(d).strip()) for s, d in confirmed
    }
    today_d = (
        _date.fromisoformat(today) if today else datetime.now(UTC).date()
    )
    with _connect(db_path) as conn:
        rows = conn.execute(
            # `estimated = 0` keeps our own cadence projections out of it: they are
            # not vendor assertions, so "no vendor asserts it" says nothing about
            # them — the estimator owns their lifecycle.
            "SELECT symbol, date, fetched_at FROM earnings_calendar "
            "WHERE source != 'edgar' AND estimated = 0 AND date >= ? AND date <= ?",
            (start, end),
        ).fetchall()
        candidates = [r for r in rows if r["symbol"] in universe]
        withdrawn: list[tuple[str, str]] = []
        for r in candidates:
            if (r["symbol"], r["date"]) in confirmed_norm:
                continue
            try:
                event_d = _date.fromisoformat(str(r["date"])[:10])
                stamp_d = _date.fromisoformat(str(r["fetched_at"])[:10])
            except ValueError:
                continue  # unparsable — keep, never guess a deletion
            if event_d > today_d or (
                event_d < today_d
                and (event_d - stamp_d).days >= _PAST_ABANDON_MARGIN_DAYS
            ):
                withdrawn.append((r["symbol"], r["date"]))
        if (
            len(withdrawn) > _PRUNE_SMALL_COUNT
            and len(withdrawn) > _PRUNE_MAX_FRACTION * len(candidates)
        ):
            logger.warning(
                "Earnings prune aborted: %d of %d in-window vendor rows read as "
                "withdrawn — confirmed set looks broken, keeping everything",
                len(withdrawn),
                len(candidates),
            )
            return 0
        if withdrawn:
            conn.executemany(
                "DELETE FROM earnings_calendar WHERE symbol = ? AND date = ?",
                withdrawn,
            )
            conn.commit()
    return len(withdrawn)


def earnings_dates_for(
    symbol: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[str]:
    """All CONFIRMED earnings dates for a symbol, ascending (estimates excluded)."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date FROM earnings_calendar "
            "WHERE symbol = ? AND estimated = 0 ORDER BY date",
            (symbol.strip().upper(),),
        ).fetchall()
    return [row["date"] for row in rows]


def next_earnings_date(
    symbol: str,
    on_date: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Earliest CONFIRMED earnings date strictly after `on_date` (the FORWARD date).

    Estimates are excluded on purpose: every caller of this function times money
    (earnings_drift entry, the exit engine's blackout). A projected date must be
    opted into via `next_earnings`, which returns the flag with it.
    """
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MIN(date) AS d FROM earnings_calendar "
            "WHERE symbol = ? AND estimated = 0 AND date > ?",
            (symbol.strip().upper(), on_date),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def next_earnings(
    symbol: str,
    on_date: str,
    *,
    include_estimated: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[str | None, bool]:
    """(next date after `on_date`, is_estimated).

    A confirmed date always wins — the estimate is only consulted when nothing
    asserted one. Callers MUST carry the flag through to whatever they display.
    """
    confirmed = next_earnings_date(symbol, on_date, db_path=db_path)
    if confirmed or not include_estimated:
        return confirmed, False
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MIN(date) AS d FROM earnings_calendar "
            "WHERE symbol = ? AND estimated = 1 AND date > ?",
            (symbol.strip().upper(), on_date),
        ).fetchone()
    estimate = row["d"] if row and row["d"] else None
    return estimate, bool(estimate)


def coverage_state(
    symbol: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Recorded EDGAR coverage verdict for a symbol (STATE_UNKNOWN if never checked)."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT state FROM earnings_coverage WHERE symbol = ?",
            (symbol.strip().upper(),),
        ).fetchone()
    return str(row["state"]) if row and row["state"] else STATE_UNKNOWN


def next_earnings_with_state(
    symbol: str,
    on_date: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[str | None, bool, str]:
    """(next date, is_estimated, coverage state) — the full answer in one read.

    No date is three different situations and they must not collapse: a name we
    can project (`dated`), a name that files no earnings at all (`non_filer` —
    an ETF, where "no earnings" is the correct answer), and a name nobody has
    looked up yet (`unknown`).
    """
    date, estimated = next_earnings(symbol, on_date, db_path=db_path)
    if date:
        return date, estimated, STATE_DATED
    state = coverage_state(symbol, db_path=db_path)
    return None, False, state if state == STATE_NON_FILER else STATE_UNKNOWN


def last_earnings_date(
    symbol: str,
    on_date: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Most recent CONFIRMED earnings date on or before `on_date` (the prior event)."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM earnings_calendar "
            "WHERE symbol = ? AND estimated = 0 AND date <= ?",
            (symbol.strip().upper(), on_date),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def earnings_hour(
    symbol: str,
    date: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Finnhub hour for a symbol/date (`bmo` / `amc` / `dmh`), or None if unknown."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT hour FROM earnings_calendar WHERE symbol = ? AND date = ?",
            (symbol.strip().upper(), date),
        ).fetchone()
    if row is None or not row["hour"]:
        return None
    hour = str(row["hour"]).strip().lower()
    return hour or None


def earnings_calendar_count(db_path: Path = DEFAULT_DB_PATH) -> int:
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0])


def upsert_coverage(
    rows: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Record per-symbol EDGAR coverage verdicts.

    Row: {symbol, state, n_filings?, last_filing_date?, median_gap_days?,
    next_estimated_date?}. `checked_at` is stamped here — it is the whole point of
    the ledger (it drives the incremental refresh's back-off).
    """
    init_earnings_calendar_tables(db_path)
    now = _now()
    payload = []
    for r in rows:
        symbol = str(r.get("symbol", "")).strip().upper()
        state = str(r.get("state") or "").strip()
        if not symbol or state not in (STATE_DATED, STATE_NON_FILER, STATE_UNKNOWN):
            continue
        payload.append(
            (
                symbol,
                state,
                int(r.get("n_filings") or 0),
                r.get("last_filing_date"),
                r.get("median_gap_days"),
                r.get("next_estimated_date"),
                now,
            )
        )
    if not payload:
        return 0
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO earnings_coverage
                (symbol, state, n_filings, last_filing_date, median_gap_days,
                 next_estimated_date, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                state = excluded.state,
                n_filings = excluded.n_filings,
                last_filing_date = excluded.last_filing_date,
                median_gap_days = excluded.median_gap_days,
                next_estimated_date = excluded.next_estimated_date,
                checked_at = excluded.checked_at
            """,
            payload,
        )
        conn.commit()
    return len(payload)


def symbols_needing_refresh(
    symbols: list[str],
    *,
    today: str,
    max_age_days: int = 7,
    non_filer_max_age_days: int = 90,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[str]:
    """The subset of `symbols` worth spending an EDGAR request on, stalest first.

    Never-checked names come first (they are the coverage hole), then the oldest
    checks. A `non_filer` verdict backs off hard — an ETF does not start filing
    earnings next week — while a name whose projected date has already come and
    gone is always re-fetched, because that is exactly when the real 8-K landed.
    """
    init_earnings_calendar_tables(db_path)
    universe = [s.strip().upper() for s in symbols if s and s.strip()]
    if not universe:
        return []
    with _connect(db_path) as conn:
        known = {
            r["symbol"]: r
            for r in conn.execute(
                "SELECT symbol, state, next_estimated_date, checked_at "
                "FROM earnings_coverage"
            ).fetchall()
        }
    today_d = _date.fromisoformat(today)
    stale: list[tuple[str, str]] = []  # (checked_at sort key, symbol)
    for sym in universe:
        row = known.get(sym)
        if row is None:
            stale.append(("", sym))
            continue
        try:
            age = (today_d - _date.fromisoformat(str(row["checked_at"])[:10])).days
        except ValueError:
            age = max_age_days + 1  # unparsable stamp — re-check rather than trust it
        limit = (
            non_filer_max_age_days
            if row["state"] == STATE_NON_FILER
            else max_age_days
        )
        elapsed = bool(
            row["next_estimated_date"] and str(row["next_estimated_date"]) <= today
        )
        if age >= limit or elapsed:
            stale.append((str(row["checked_at"]), sym))
    stale.sort()
    return [sym for _stamp, sym in stale]


def coverage_summary(db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """{state: symbols} plus how many carry an estimated vs confirmed forward date."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        out = {
            str(r["state"]): int(r["n"])
            for r in conn.execute(
                "SELECT state, COUNT(*) AS n FROM earnings_coverage GROUP BY state"
            ).fetchall()
        }
        out["estimated_rows"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM earnings_calendar WHERE estimated = 1"
            ).fetchone()[0]
        )
    return out
