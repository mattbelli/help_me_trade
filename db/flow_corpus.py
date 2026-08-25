"""Store for the whole-market OPRA directional-flow corpus.

Its own database (``data/flow_corpus.db``) — research-scale capture, kept out of
the 1.6GB ``trading.db`` for the same reason as the options-chain sampler.

Grain is the Tier-3 rollup from `References/FLOW_BASELINE_PREREG_2026-07-26.md`:
per session, per underlying, per right, per DTE bucket, per moneyness bucket,
per side. Both classification arms ride on every row —
``side`` is the midpoint rule (PRIMARY, per Addendum A) and ``strict_side`` is
the at-the-quote rule (SECONDARY) — so a finding can be shown under both without
a second pass over 11M prints.

Two rules this schema exists to enforce, both learned the hard way:
  * **Direction is never aggregated away.** ``options_flow_daily`` was rolled up
    from option bars, has no side column, and is therefore useless for exactly
    the question this corpus was bought to answer.
  * **Dispersion is kept, not just totals.** A z-score needs a variance, so the
    sum of squares travels with the sum.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "flow_corpus.db"

# Bump when a bucket boundary or a classification rule changes. Rows carry it so
# incomparable vintages can never be silently pooled — the same failure mode as
# retuning a detector threshold and invalidating every historical baseline.
#
# ⚠️ The DEFAULT flipped to "6" on 2026-07-28 — the Addendum E migration,
# executed under Addendum F (founder-blessed same day): at 6, `multileg` comes
# from joined Alpaca condition codes with the heuristic fallback PER PRINT
# (per Addendum F the 98% floor is a measurement/reporting gate, not an
# adoption gate — a below-floor session still adopts its labels and records
# its coverage), the raw code is the `cond` grain in the rollup key, and
# init_db rebuilds the table. One definition corpus-wide; per-session
# provenance in `multileg_source`/`label_join_rate`/`label_coverage` and the
# ≥98% robustness subset in `data/alpaca_conditions/join_ledger.json`.
# The env override exists for SCRATCH testing only; at 6 init_db drops and
# rebuilds `flow_rollup`.
SCHEMA_VERSION = int(os.environ.get("FLOW_CORPUS_SCHEMA_VERSION", "7"))

# schema_version 6 = Addendum E (code-based multileg + cond grain).
LABELED = SCHEMA_VERSION >= 6

DTE_BUCKETS = ("0-7", "8-30", "31-90", "90+")
# 'unknown' was two different things wearing one label, and it is 38.75% of a
# session's premium (measured 2025-07-28), so the conflation is not a rounding
# error. Cash-settled index roots have no spot BY CONSTRUCTION -- SPX and SPXW
# alone are $9.67B of the $10.20B in that bucket -- while an ordinary name with
# no bar is a data fault. Pooled, the structural mass hides the fault; split, the
# fault is at least visible.
# ⚠️ CORRECTED 2026-07-27 by review -- the first version of this comment claimed
# `no_spot_missing` runs "~$19M/session" and that a missed index root "would move
# it to billions". Both are wrong. Measured over 138 rolled sessions the bucket
# runs $41.3M to $7,196.6M (mean $256.0M), so the magnitude discriminator is
# ALREADY SATURATED and cannot identify a missed root: the largest covered roots
# are SPX $9,891M but VIX $89.8M / RUT $94.9M / NDX $234M / XSP $31.9M, all well
# inside the bucket's own swing. The tail is driven by ADJUSTED CONTRACTS whose
# premium is inflated by the hardcoded 100 multiplier (roadmap 1.5) -- WOLF1 alone
# is $7,082M on 2025-12-19 and $2,740M on 2026-01-16, OPEN1 $2,188M.
# ⚠️ It is also NOT a clean delisting list, which an earlier note claimed. It is
# four unrelated causes mixed: adjusted contracts (WOLF1, OPEN1, BABA2), ticker
# format breaks (BRKB, $3.8B over 110 sessions), genuine corporate actions (WBA,
# PARA, SATS), and plain EQUITY-BAR COVERAGE GAPS on live, actively traded names
# (CMA, PSTG, MMC, CTRA, CFLT, MPW appear in all 110 of their sessions; AZN has no
# bars inside the range at all). Those live names get NO moneyness in the corpus
# -- a real data gap to close, not a corporate action. Read the per-root breakdown
# before drawing any conclusion from this bucket.
MNY_BUCKETS = ("itm", "atm", "otm2_10", "otm10p", "no_spot_index", "no_spot_missing")
# `wide_spread` joins the vocabulary at v7 (2026-07-29). Prereg clause 1 leaves
# those prints unlabelled, but pooling them into `unclassified` with no-quote
# prints would make the wide-spread VOLUME per name-day unrecoverable — and that
# volume is a covariate grading needs, since a name-day whose tilt survives on
# 40% of its quoted prints is not compositionally the same object as one that
# keeps 95%. Recording it beats gating on it: a gate deleted 45-61% of name-days
# (see FLOW_ROADMAP §1.25), a covariate lets the question be asked instead.
# Consumers filtering `side IN ('buy','sell')` are unaffected.
SIDES = ("buy", "sell", "unclassified", "wide_spread")

# Cash-settled index roots: no underlying share trades, so no equity bar can ever
# exist and moneyness is unbucketable rather than missing. Deliberately a short
# list of the roots that actually print -- OPRA's definition record carries NO
# usable discriminator (verified 2026-07-27 on 1,592,395 contracts: `cfi` is
# empty, `security_type` is 'OPT' for everything, `underlying_product` is 255 for
# everything), so a curated list is the only option. Being incomplete is safe by
# construction: a root we miss lands in `no_spot_missing`, which is monitored.
INDEX_ROOTS = frozenset({
    "SPX", "SPXW", "XSP", "NDX", "NDXP", "XND", "RUT", "RUTW", "MRUT",
    "VIX", "VIXW", "DJX", "OEX", "XEO", "RVX", "SPESG", "MXEA", "MXEF",
})

# Columns the current vintage must have. init_db rebuilds when any is absent --
# see the comment there for why dropping is safe for this table specifically.
# At v6 `cond` joins the set, so flipping SCHEMA_VERSION triggers the rebuild.
_ROLLUP_COLUMNS = frozenset({
    "date", "underlying", "right", "dte_bucket", "mny_bucket", "side",
    "strict_side", "prints", "orders", "contracts", "premium_usd", "premium_sq",
    "premium_max", "u_premium", "u_premium_clipped", "multileg", "schema_version",
    "premium_sq_orders",
}) | (frozenset({"cond"}) if LABELED else frozenset())


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        # multileg is part of the PRIMARY KEY and SQLite cannot ALTER a key, so a
        # table that predates it must be rebuilt rather than extended. Dropping is
        # safe here and nowhere else: flow_rollup is derived data, reproducible
        # from the raw .dbn.zst prints retained on disk, and every pre-existing row
        # is already fenced out by ingested_dates()'s schema_version filter -- it
        # would be re-rolled before any query could read it. The rows are stale,
        # not precious. This runs before CREATE so the recreate is the same
        # statement that builds a fresh database, never a second divergent copy.
        # Driven off the whole column set rather than one name so a later column
        # cannot be added to the DDL and silently skip the rebuild.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(flow_rollup)")}
        if cols and not _ROLLUP_COLUMNS <= cols:
            conn.execute("DROP TABLE flow_rollup")
        # The v6 pieces of the DDL. `cond` is the raw Alpaca condition code the
        # print matched ('' where none joined and the flag fell back to the
        # heuristic) — a GRAIN like multileg, in the key, so finer splits
        # (electronic vs auction vs floor vs tied) stay available without
        # another schema change, exactly as Addendum E registered.
        cond_col = "cond TEXT NOT NULL," if LABELED else ""
        cond_key = ", cond" if LABELED else ""
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS flow_rollup (
                date TEXT NOT NULL,
                underlying TEXT NOT NULL,
                right TEXT NOT NULL,
                dte_bucket TEXT NOT NULL,
                mny_bucket TEXT NOT NULL,
                side TEXT NOT NULL,
                strict_side TEXT NOT NULL,
                prints INTEGER NOT NULL,
                -- Distinct (ts_event, instrument_id) groups behind those prints:
                -- one order that swept several venues arrives as N prints but is
                -- ONE observation, and counting it N times inflates every
                -- dispersion statistic computed off this table. **`orders` is the
                -- N for an order-grain z-score, used ONLY with
                -- `premium_sq_orders`; `prints` is retained unchanged because the
                -- prereg registered it, and pairs with `premium_sq`.**
                -- Measured 2025-07-28: 6,930,544 prints -> 6,464,608 orders
                -- (6.72% pooled), but the inflation is CONCENTRATED -- median
                -- 5.85% / p90 9.44% / max 15.97% across the 480 names with >=1000
                -- prints, worst in thin, heavily-swept names (TLRY, SNAP, CIFR,
                -- BITF). At p90 that overstates sqrt(N) by 1.051x, and it does so
                -- exactly where measurement 1.7 expects false positives.
                -- ⚠️⚠️ PAIR IT WITH `premium_sq_orders`, NEVER WITH `premium_sq`.
                -- `premium_sq` is a sum of PER-PRINT squares; feeding it an
                -- order-grain N breaks the Cauchy-Schwarz bound that keeps a
                -- variance non-negative, because for one sweep split across two
                -- venues p1^2 + p2^2 - (p1+p2)^2 = -2*p1*p2 < 0. Measured on the
                -- live corpus: 411,218 rows (1.55%) go NEGATIVE that way and
                -- their sd is NaN, while the registered pairing produces exactly
                -- 1 (float rounding). Caught in review 2026-07-27 before the
                -- 260-session re-roll, i.e. while the fix was still free.
                -- Deliberately NOT a re-signing: legs inside a group disagree on
                -- side in only 1.371% of multi-print groups carrying 0.167% of
                -- premium, so imposing one sign per group would move almost
                -- nothing while breaking a registered per-print definition.
                -- ⚠️ A group whose legs disagree on side straddles two buckets and
                -- is counted in EACH -- necessary, or the sell half of every mixed
                -- sweep would have premium with no N behind it. So SUM(orders)
                -- over buckets slightly exceeds the session's distinct group count
                -- (6,472,736 vs 6,464,608 on 2025-07-28, +0.13%). Correct per
                -- bucket, which is the grain every statistic uses; do not sum it
                -- across buckets and call the result a session N.
                orders INTEGER NOT NULL,
                contracts INTEGER NOT NULL,
                premium_usd REAL NOT NULL,
                premium_sq REAL NOT NULL,
                -- The order-grain second moment: each (bucket, group) pair's
                -- premium summed FIRST, then squared, then accumulated. This is
                -- the statistic "one sweep is one observation" actually implies,
                -- and it is the only one `orders` may be divided by. `premium_sq`
                -- is left exactly as pre-registered and still pairs with
                -- `prints`; both arms are stored so neither grain can be chosen
                -- after seeing the answer -- the same doctrine as u_premium /
                -- u_premium_clipped and the multileg grain.
                premium_sq_orders REAL NOT NULL,
                premium_max REAL NOT NULL,
                u_premium REAL NOT NULL,
                -- Same sum with u clipped to [-1, 1]. u is a RATIO with the
                -- half-spread underneath, so a print far from a tight quote
                -- explodes it (measured range [-61, +90]) and |u|>2 -- 0.40% of
                -- prints -- carries 171.92% of sum(u*premium). Past the touch a
                -- larger |u| measures quote quality, not conviction: 36% of those
                -- prints share a ts_event with a different instrument (multi-leg
                -- legs priced off their own NBBO), the rest sit against stale or
                -- wide quotes. u_premium stays EXACTLY as pre-registered so the
                -- registered test still runs as written; this is the labelled
                -- post-hoc robustness counterpart. Agreement between the two is
                -- the directional read -- disagreement means abstain, not pick.
                u_premium_clipped REAL NOT NULL,
                -- 1 when the print shared its ts_event with a print on a
                -- DIFFERENT instrument: the multi-leg signature. A spread leg is
                -- priced by the firm allocating the package and need not sit
                -- inside that leg's own NBBO, so its midpoint side is unreliable
                -- -- and unreliable in ALL THREE measures, since they share one
                -- per-print sign. Measured 2025-07-28 (in-sample): 12.96% of
                -- prints, 33.33% of premium, and excluding them FLIPS the session
                -- sign of net_premium (+553.6M -> -219.3M), u_premium_clipped
                -- (+95.4M -> -84.3M) and u_premium (+366.4M -> -176.5M).
                -- It is a grain, not a filter: both arms are stored so neither
                -- inclusion nor exclusion can be chosen after seeing the answer.
                -- Heuristic, not ground truth -- OPRA tcbbo `flags` is constant
                -- 194 across all 6.95M prints and marks nothing (measured by
                -- tools/tcbbo_flags_probe.py). ts_event is true-ns, so collisions
                -- are real simultaneity: ~1 coincidence expected per session.
                multileg INTEGER NOT NULL,
                {cond_col}
                schema_version INTEGER NOT NULL,
                PRIMARY KEY (date, underlying, right, dte_bucket, mny_bucket,
                             side, strict_side, multileg{cond_key})
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rollup_und_date "
            "ON flow_rollup (underlying, date)"
        )
        # The spot each session's moneyness was computed against. Stored so a
        # bucket can be audited or recomputed without guessing which close was
        # used, and so a later intraday-spot upgrade is a diff, not a mystery.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_spot (
                date TEXT NOT NULL,
                underlying TEXT NOT NULL,
                spot REAL NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (date, underlying)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equity_bars (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                o REAL, h REAL, l REAL, c REAL, v INTEGER,
                adjustment TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (symbol, date, adjustment)
            )
            """
        )
        # Ingest ledger. A session absent here was never processed; a session
        # present with prints=0 is a recorded holiday, not a silent hole.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_sessions (
                date TEXT PRIMARY KEY,
                prints INTEGER NOT NULL,
                rows INTEGER NOT NULL,
                underlyings INTEGER NOT NULL,
                unresolved_prints INTEGER NOT NULL,
                no_spot_prints INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                ingested_at TEXT NOT NULL
            )
            """
        )
        # Addendum E audit trail, additive and nullable so v5 rows are
        # untouched: which multileg source labeled the session
        # ('codes'|'heuristic'), the label join rate the floor was judged on,
        # and the share of joined prints that actually carried a code.
        sess_cols = {r["name"]
                     for r in conn.execute("PRAGMA table_info(corpus_sessions)")}
        for col, typ in (("multileg_source", "TEXT"),
                         ("label_join_rate", "REAL"),
                         ("label_coverage", "REAL")):
            if col not in sess_cols:
                conn.execute(f"ALTER TABLE corpus_sessions ADD COLUMN {col} {typ}")


def delete_session_rows(date: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Clear a session before re-rolling it.

    Row-scoped INSERT OR REPLACE is NOT enough: the primary key contains
    ``mny_bucket``, and a re-roll can legitimately move a row to a different
    bucket (e.g. once spot is available, a no-spot row becomes 'otm2_10'). The old
    row keeps its old key and survives, so the session ends up counted twice.
    Observed 2026-07-26 — premium doubled to $45.6B under a phantom 'unknown'
    bucket. Session-scoped delete is the only idempotent unit here.
    """
    with connect(db_path) as conn:
        cur = conn.execute("DELETE FROM flow_rollup WHERE date = ?", (date,))
        return cur.rowcount


def upsert_rollup(rows: list[tuple], db_path: Path = DEFAULT_DB_PATH) -> int:
    """Row shape follows the vintage: at v6 each tuple carries `cond` between
    `multileg` and `schema_version` (matching rollup_session's emit order)."""
    if not rows:
        return 0
    cond = "cond, " if LABELED else ""
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO flow_rollup (date, underlying, right, "
            "dte_bucket, mny_bucket, side, strict_side, prints, orders, "
            "contracts, premium_usd, premium_sq, premium_sq_orders, "
            f"premium_max, u_premium, u_premium_clipped, multileg, {cond}"
            "schema_version) "
            f"VALUES ({','.join('?' * (18 + LABELED))})",
            rows,
        )
    return len(rows)


def upsert_spot(rows: list[tuple], db_path: Path = DEFAULT_DB_PATH) -> int:
    if not rows:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO corpus_spot (date, underlying, spot, source) "
            "VALUES (?,?,?,?)",
            rows,
        )
    return len(rows)


def upsert_bars(rows: list[tuple], db_path: Path = DEFAULT_DB_PATH) -> int:
    if not rows:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO equity_bars "
            "(symbol, date, o, h, l, c, v, adjustment, source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def bars_for_date(date: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, float]:
    with connect(db_path) as conn:
        return {
            r["symbol"]: r["c"]
            for r in conn.execute(
                "SELECT symbol, c FROM equity_bars "
                "WHERE date = ? AND adjustment = 'split' AND c IS NOT NULL",
                (date,),
            )
        }


def bars_before_date(
    date: str, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, tuple[float, str]]:
    """Each symbol's most recent split-adjusted close STRICTLY BEFORE ``date``.

    This is the spot reference Addendum B registered, and using the session's own
    close instead was a live look-ahead: moneyness would be bucketed against the
    move that had just happened, so every OTM label was conditioned on the day's
    realized return. Fixed 2026-07-27.

    Strictly-before rather than date-minus-one-day so weekends, holidays and
    halted names resolve to a real bar instead of a hole. Returns the bar's date
    alongside the close so the caller can report how stale it is — a delisted name
    can legitimately return a close from weeks earlier, which is a data-quality
    fact worth surfacing rather than silently bucketing against.
    """
    with connect(db_path) as conn:
        return {
            r["symbol"]: (r["c"], r["date"])
            for r in conn.execute(
                "SELECT symbol, c, date FROM ("
                "  SELECT symbol, c, date, ROW_NUMBER() OVER ("
                "    PARTITION BY symbol ORDER BY date DESC) rn"
                "  FROM equity_bars"
                "  WHERE date < ? AND adjustment = 'split' AND c IS NOT NULL"
                ") WHERE rn = 1",
                (date,),
            )
        }


def record_session(
    date: str,
    *,
    prints: int,
    rows: int,
    underlyings: int,
    unresolved_prints: int,
    no_spot_prints: int,
    ingested_at: str,
    multileg_source: str | None = None,
    label_join_rate: float | None = None,
    label_coverage: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Ledger one processed session.

    ``no_spot_prints`` counts ONLY the ``no_spot_missing`` arm from
    schema_version 4 onward — prints on a name that should have had an equity bar
    and did not. Index roots are excluded because they can never have one, and
    including them made this column read 14% of prints when the actual fault rate
    was 0.03%. Rows at earlier versions carry the old pooled meaning and are
    fenced out by ``ingested_dates()``.
    """
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO corpus_sessions (date, prints, rows, "
            "underlyings, unresolved_prints, no_spot_prints, schema_version, "
            "ingested_at, multileg_source, label_join_rate, label_coverage) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (date, prints, rows, underlyings, unresolved_prints,
             no_spot_prints, SCHEMA_VERSION, ingested_at,
             multileg_source, label_join_rate, label_coverage),
        )


def ingested_dates(db_path: Path = DEFAULT_DB_PATH) -> set[str]:
    with connect(db_path) as conn:
        return {
            r["date"]
            for r in conn.execute(
                "SELECT date FROM corpus_sessions WHERE schema_version = ?",
                (SCHEMA_VERSION,),
            )
        }


# --- agent history reads (perception `flow_history`) --------------------------------
# Read-only. The corpus is a separate 16GB file; never open it through the
# trading.db path the registry passes. Pairing law: `premium_sq` goes with
# `prints`; `premium_sq_orders` goes with `orders`. Mixing them is the
# 1.55% negative-variance footgun in the DDL comment above.

_ET = ZoneInfo("America/New_York")
RIGHTS = ("C", "P")
_DEFAULT_SESSIONS = 60
_DRILL_CAP = 400
_GROUP_BY = {
    "right": "right",
    "dte_bucket": "dte_bucket",
    "mny_bucket": "mny_bucket",
    "side": "side",
    "strict_side": "strict_side",
    "cond": "cond",
    "multileg": "multileg",
}
_DIM_VALUES = {
    "right": RIGHTS,
    "dte_bucket": DTE_BUCKETS,
    "mny_bucket": MNY_BUCKETS,
    "side": SIDES,
    "strict_side": SIDES,
    "multileg": ("0", "1"),
}


def connect_ro(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the corpus without creating it or taking a write lock."""
    uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def order_grain_variance(
    premium_usd: float, premium_sq_orders: float, orders: int
) -> float | None:
    """Variance of order-sized premiums. None when there are no orders."""
    if orders <= 0:
        return None
    mean = premium_usd / orders
    return premium_sq_orders / orders - mean * mean


def history_query(
    filters: dict[str, Any] | None = None,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Marginals (default) or rarity rank for one underlying.

    Missing/locked corpus → one `unavailable` row, never an exception.
    Missing symbol → error naming `flow_perception`, never a full-table scan.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not path.is_file():
        return [_unavailable("flow corpus file is missing")]
    try:
        conn = connect_ro(path)
    except sqlite3.Error as exc:
        return [_unavailable(f"flow corpus could not be opened ({exc})")]
    try:
        return _history_query_conn(conn, filters or {})
    except sqlite3.Error as exc:
        return [_unavailable(f"flow corpus read failed ({exc})")]
    finally:
        conn.close()


def _unavailable(note: str) -> dict[str, Any]:
    return {
        "unavailable": True,
        "note": note + " — not 'nothing unusual'; the history file is unreadable",
    }


def _history_query_conn(
    conn: sqlite3.Connection, filters: dict[str, Any]
) -> list[dict[str, Any]]:
    symbol = str(
        filters.get("symbol") or filters.get("underlying") or filters.get("ticker") or ""
    ).strip().upper()
    if not symbol:
        return [
            {
                "error": (
                    "symbol is required — this index is (underlying, date). "
                    "A market-wide scan of the corpus is a full-table read. "
                    "For market-wide unusual-vs-peers, query flow_perception."
                )
            }
        ]

    group_by = filters.get("group_by")
    if isinstance(group_by, (list, tuple)):
        return [
            {
                "error": "group_by takes ONE of: " + "|".join(_GROUP_BY),
                "available": list(_GROUP_BY),
            }
        ]
    if group_by is not None and str(group_by).strip() != "":
        key = str(group_by).strip()
        if key not in _GROUP_BY:
            return [
                {
                    "error": f"unknown group_by {key!r}",
                    "available": list(_GROUP_BY),
                }
            ]

    try:
        limit = int(filters["limit"]) if filters.get("limit") is not None else _DRILL_CAP
    except (TypeError, ValueError):
        return [{"error": "limit must be an int"}]
    limit = max(1, min(limit, _DRILL_CAP))

    since = _opt_str(filters.get("since"))
    until = _opt_str(filters.get("until"))
    window = _session_window(conn, since, until)
    if window["corpus_through"] is None:
        return [_unavailable("flow corpus has no sessions")]

    dim_err = _validate_dims(filters)
    if dim_err:
        return [dim_err]

    premium_raw = filters.get("premium_usd")
    if premium_raw is not None and str(premium_raw).strip() != "":
        try:
            premium = float(premium_raw)
        except (TypeError, ValueError):
            return [{"error": "premium_usd must be a number"}]
        return [
            _rarity_row(
                conn,
                symbol=symbol,
                premium=premium,
                window=window,
                filters=filters,
            )
        ]

    return _marginals(
        conn,
        symbol=symbol,
        window=window,
        group_by=str(group_by).strip() if group_by else None,
        limit=limit,
        filters=filters,
    )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _session_window(
    conn: sqlite3.Connection, since: str | None, until: str | None
) -> dict[str, Any]:
    row = conn.execute("SELECT MIN(date), MAX(date) FROM corpus_sessions").fetchone()
    through = row[1] if row else None
    until_s = until or through
    since_s = since
    if since_s is None and until_s is not None:
        dates = [
            r[0]
            for r in conn.execute(
                "SELECT date FROM corpus_sessions WHERE date <= ? "
                "ORDER BY date DESC LIMIT ?",
                (until_s, _DEFAULT_SESSIONS),
            )
        ]
        since_s = dates[-1] if dates else until_s
    stale_days = None
    if through:
        today = datetime.now(_ET).date()
        stale_days = (today - datetime.fromisoformat(through).date()).days
    return {
        "since": since_s,
        "until": until_s,
        "corpus_through": through,
        "stale_days": stale_days,
    }


def _validate_dims(filters: dict[str, Any]) -> dict[str, Any] | None:
    for dim, allowed in _DIM_VALUES.items():
        raw = filters.get(dim)
        if raw is None or str(raw).strip() == "":
            continue
        value = str(raw).strip()
        if dim == "multileg" and value in ("0", "1", "0.0", "1.0"):
            value = value[0]
        if value not in allowed:
            return {
                "error": f"unknown {dim} {value!r}",
                "available": list(allowed),
            }
    return None


def _where(
    symbol: str,
    window: dict[str, Any],
    filters: dict[str, Any],
) -> tuple[str, list[Any]]:
    clauses = ["underlying = ?", "date >= ?", "date <= ?"]
    params: list[Any] = [symbol, window["since"], window["until"]]
    for dim, col in _GROUP_BY.items():
        raw = filters.get(dim)
        if raw is None or str(raw).strip() == "":
            continue
        value: Any = str(raw).strip()
        if dim == "multileg":
            value = int(float(value))
        clauses.append(f"{col} = ?")
        params.append(value)
    return " AND ".join(clauses), params


def _stamp(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "since": window["since"],
        "until": window["until"],
        "corpus_through": window["corpus_through"],
        "stale_days": window["stale_days"],
        "side_is_not_sentiment": True,
        "note": (
            "side is a midpoint/quote classification, not sentiment — packages "
            "are ~53% of premium and a multi-leg flip reverses the sign. "
            "Rarity is a rank in this name's bucket, not a forecast. "
            "Forward screens on this fingerprint were pre-registered NULL twice."
        ),
    }


def _marginals(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    window: dict[str, Any],
    group_by: str | None,
    limit: int,
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    where, params = _where(symbol, window, filters)
    row = {
        "symbol": symbol,
        **_stamp(window),
        "premium_usd": 0.0,
        "orders": 0,
        "prints": 0,
    }
    totals = conn.execute(
        f"SELECT COALESCE(SUM(premium_usd),0), COALESCE(SUM(orders),0), "
        f"COALESCE(SUM(prints),0) FROM flow_rollup WHERE {where}",
        params,
    ).fetchone()
    row["premium_usd"] = totals[0]
    row["orders"] = totals[1]
    row["prints"] = totals[2]

    dims = [group_by] if group_by else [
        "right", "dte_bucket", "mny_bucket", "side", "strict_side", "multileg"
    ]
    for dim in dims:
        col = _GROUP_BY[dim]
        drill = conn.execute(
            f"SELECT {col} AS bucket, SUM(premium_usd) AS premium_usd, "
            f"SUM(orders) AS orders, SUM(prints) AS prints "
            f"FROM flow_rollup WHERE {where} GROUP BY {col} "
            f"ORDER BY premium_usd DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        row[dim] = [
            {
                "bucket": _plain(r["bucket"]),
                "premium_usd": r["premium_usd"],
                "orders": r["orders"],
                "prints": r["prints"],
            }
            for r in drill
        ]

    daily = conn.execute(
        f"SELECT date, SUM(premium_usd) AS premium_usd, SUM(orders) AS orders, "
        f"SUM(prints) AS prints FROM flow_rollup WHERE {where} "
        f"GROUP BY date ORDER BY date LIMIT ?",
        [*params, limit],
    ).fetchall()
    row["daily"] = [
        {
            "date": r["date"],
            "premium_usd": r["premium_usd"],
            "orders": r["orders"],
            "prints": r["prints"],
        }
        for r in daily
    ]
    return [row]


def _rarity_row(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    premium: float,
    window: dict[str, Any],
    filters: dict[str, Any],
) -> dict[str, Any]:
    missing = [
        k for k in ("right", "dte_bucket", "mny_bucket")
        if not _opt_str(filters.get(k))
    ]
    if missing:
        return {
            "error": (
                "rarity ranks inside (underlying, right, dte_bucket, mny_bucket) "
                "— pass " + ", ".join(missing)
            ),
            "available": {k: list(_DIM_VALUES[k]) for k in ("right", "dte_bucket", "mny_bucket")},
        }
    where, params = _where(symbol, window, filters)
    agg = conn.execute(
        f"SELECT COALESCE(SUM(orders),0) AS n, "
        f"COALESCE(SUM(CASE WHEN premium_max < ? THEN orders ELSE 0 END),0) "
        f"AS smaller, "
        f"MAX(premium_max) AS biggest, "
        f"COALESCE(SUM(premium_usd),0) AS premium_usd, "
        f"COALESCE(SUM(premium_sq_orders),0) AS premium_sq_orders "
        f"FROM flow_rollup WHERE {where}",
        [premium, *params],
    ).fetchone()
    biggest_date = None
    if agg["biggest"] is not None:
        hit = conn.execute(
            f"SELECT date FROM flow_rollup WHERE {where} AND premium_max = ? "
            f"ORDER BY date DESC LIMIT 1",
            [*params, agg["biggest"]],
        ).fetchone()
        biggest_date = hit["date"] if hit else None
    n = int(agg["n"] or 0)
    smaller = int(agg["smaller"] or 0)
    var = order_grain_variance(agg["premium_usd"], agg["premium_sq_orders"], n)
    # Rank uses cell max: an order counts as smaller only when that cell's
    # largest order was smaller. Intra-cell sizes are collapsed in the rollup,
    # so larger_than_pct is a lower bound, never a z-score or a forecast.
    return {
        "symbol": symbol,
        "premium_usd": premium,
        "right": str(filters["right"]).strip(),
        "dte_bucket": str(filters["dte_bucket"]).strip(),
        "mny_bucket": str(filters["mny_bucket"]).strip(),
        "orders_smaller": smaller,
        "reference_n": n,
        "larger_than_pct": round(100.0 * smaller / n, 4) if n else None,
        "biggest_seen_usd": agg["biggest"],
        "biggest_seen_date": biggest_date,
        "rank_grain": "orders",
        "rank_method": "cell_max_bound",
        "order_variance_nonneg": var is None or var >= -1e-6,
        **_stamp(window),
    }


def _plain(value: Any) -> Any:
    if isinstance(value, int) and value in (0, 1):
        return value
    return value if value is not None else None
