"""Forward options-chain corpus — the appender that keeps the IV history alive.

Its own database (``data/options_chain_history.db``), deliberately NOT inside
``trading.db``: this is research-scale capture (hundreds of thousands of rows a
session) and the live trading DB should not carry it. It is the forward twin of
``data/options_iv_history.db``, the frozen DoltHub snapshot that ends 2026-07-17
— that corpus insures the past and cannot be extended, so everything after it
has to come from here.

Three tables plus a completeness sidecar:
  * ``chain_snapshots`` — per contract per session, inside the stored band.
  * ``iv_daily``        — per underlying per session; the IV-rank input. Sampled
    AFTER the close, off quotes frozen at 16:00 — i.e. the conventional 4:00 PM
    surface.
  * ``iv_preclose``     — the same row shape sampled ~15 min BEFORE the close. A
    separate table, not a second key on ``iv_daily``, because every reader of
    ``iv_daily`` treats one row per session as "the close". Exists to measure
    the "Last Fifteen Minutes" result (SSRN 7279624: with 0DTE the close is the
    expiration boundary, and the 3:45 PM ATM short-dated IV predicts next-day
    realized variance far better than the 4:00 PM one) on OUR data before any
    vol model leans on either timestamp.
  * ``sampler_universe``— APPEND-ONLY. Once a symbol is sampled it keeps being
    sampled, because an IV rank is a percentile against that symbol's own
    trailing year: a symbol that drops off a watchlist and returns later would
    otherwise carry a hole its rank silently averages over.
  * ``sampler_runs``    — per-session provenance, so an absence is interpretable
    (quiet session vs sweep never ran vs vendor bars had not rolled).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from db.base import ROOT, now_iso
from db.base import connect as _connect

CHAIN_DB_PATH = Path(
    os.environ.get("OPTIONS_CHAIN_DB_PATH", str(ROOT / "data" / "options_chain_history.db"))
)


# One row shape for both IV tables, so a 15:45 row and a close row differ ONLY
# in when the chain was read — the comparison is meaningless otherwise.
_IV_ROW_COLUMNS = """
                session TEXT NOT NULL,
                underlying TEXT NOT NULL,
                spot REAL,
                atm_iv_30d REAL,
                atm_iv_front REAL,
                front_dte INTEGER,
                hv_ewma REAL,
                vrp REAL,
                call_volume INTEGER NOT NULL DEFAULT 0,
                put_volume INTEGER NOT NULL DEFAULT 0,
                call_oi INTEGER NOT NULL DEFAULT 0,
                put_oi INTEGER NOT NULL DEFAULT 0,
                contracts_observed INTEGER NOT NULL DEFAULT 0,
                contracts_stored INTEGER NOT NULL DEFAULT 0,
                bars_current INTEGER NOT NULL DEFAULT 0,
                captured_at TEXT NOT NULL,
                -- 'deep' = the whole board was read, so the volume/OI columns are
                -- true board totals. 'wide' = an IV reading only: the fetch was a
                -- slice, so volume/OI were NOT COLLECTED and the zeros in those
                -- columns are placeholders, never measurements. Check this column
                -- before aggregating anything but the IV/HV/VRP fields.
                tier TEXT NOT NULL DEFAULT 'deep',
                PRIMARY KEY (session, underlying)
"""


def init_chain_tables(db_path: Path = CHAIN_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chain_snapshots (
                session TEXT NOT NULL,
                occ TEXT NOT NULL,
                underlying TEXT NOT NULL,
                expiration TEXT NOT NULL,
                strike REAL NOT NULL,
                opt_type TEXT NOT NULL,
                style TEXT,
                spot REAL,
                bid REAL,
                ask REAL,
                bid_size INTEGER,
                ask_size INTEGER,
                last_price REAL,
                volume INTEGER NOT NULL DEFAULT 0,
                bar_date TEXT,
                open_interest INTEGER,
                oi_date TEXT,
                iv REAL,
                delta REAL,
                gamma REAL,
                theta REAL,
                vega REAL,
                rho REAL,
                captured_at TEXT NOT NULL,
                PRIMARY KEY (session, occ)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chain_underlying_session "
            "ON chain_snapshots (underlying, session)"
        )
        # The OI backfill lands a session late and addresses rows by contract,
        # so it needs the contract to be findable without the session prefix.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chain_occ_session ON chain_snapshots (occ, session)"
        )
        conn.execute(f"CREATE TABLE IF NOT EXISTS iv_daily ({_IV_ROW_COLUMNS})")
        conn.execute(f"CREATE TABLE IF NOT EXISTS iv_preclose ({_IV_ROW_COLUMNS})")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sampler_universe (
                underlying TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                source TEXT,
                tier TEXT NOT NULL DEFAULT 'deep'
            )
            """
        )
        # Pre-tier databases carry no `tier`; existing rows are the names we might
        # actually trade, so 'deep' is the right default for them.
        for table in ("sampler_universe", "iv_daily"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "tier" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN tier TEXT NOT NULL DEFAULT 'deep'"
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sampler_runs (
                session TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                symbols_attempted INTEGER NOT NULL DEFAULT 0,
                symbols_ok INTEGER NOT NULL DEFAULT 0,
                contracts_written INTEGER NOT NULL DEFAULT 0,
                oi_backfilled INTEGER NOT NULL DEFAULT 0,
                failures TEXT
            )
            """
        )
        conn.commit()


def register_universe(
    symbols: list[str], *, source: str = "watchlist", tier: str = "deep",
    db_path: Path = CHAIN_DB_PATH
) -> int:
    """Add symbols to the append-only universe. Returns how many were new.

    ``tier`` is 'deep' (store the whole banded board — names we might trade) or
    'wide' (an IV reading only). A symbol already registered deep is never demoted
    to wide by a later call: breadth must not cost us depth on a name we hold.
    """
    stamp = now_iso()
    rows = [
        (s.strip().upper(), stamp, source, tier)
        for s in symbols
        if isinstance(s, str) and s.strip()
    ]
    if not rows:
        return 0
    with _connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM sampler_universe").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO sampler_universe (underlying, first_seen, source, tier) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        if tier == "deep":
            # Promotion is allowed and is the only tier change there is.
            conn.executemany(
                "UPDATE sampler_universe SET tier='deep' WHERE underlying=? AND tier<>'deep'",
                [(r[0],) for r in rows],
            )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM sampler_universe").fetchone()[0]
    return after - before


def universe_symbols(db_path: Path = CHAIN_DB_PATH, tier: str | None = None) -> list[str]:
    """Every symbol ever registered — the sweep runs over this, not today's watchlist."""
    sql = "SELECT underlying FROM sampler_universe"
    params: tuple = ()
    if tier:
        sql += " WHERE tier = ?"
        params = (tier,)
    with _connect(db_path) as conn:
        return [str(r["underlying"]) for r in conn.execute(sql + " ORDER BY underlying", params)]


def universe_tiers(db_path: Path = CHAIN_DB_PATH) -> dict[str, str]:
    """Symbol -> tier, for a sweep that treats the two differently."""
    with _connect(db_path) as conn:
        return {
            str(r["underlying"]): str(r["tier"] or "deep")
            for r in conn.execute("SELECT underlying, tier FROM sampler_universe")
        }


def write_chain_rows(rows: list[dict], *, db_path: Path = CHAIN_DB_PATH) -> int:
    """Insert one session's banded contract rows. Re-running a session replaces it."""
    if not rows:
        return 0
    stamp = now_iso()
    payload = [
        (
            r["session"], r["occ"], r["underlying"], r["expiration"], r["strike"],
            r["opt_type"], r.get("style"), r.get("spot"), r.get("bid"), r.get("ask"),
            r.get("bid_size"), r.get("ask_size"), r.get("last_price"),
            r.get("volume") or 0, r.get("bar_date"), r.get("open_interest"),
            r.get("oi_date"), r.get("iv"), r.get("delta"), r.get("gamma"),
            r.get("theta"), r.get("vega"), r.get("rho"), stamp,
        )
        for r in rows
    ]
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO chain_snapshots (
                session, occ, underlying, expiration, strike, opt_type, style,
                spot, bid, ask, bid_size, ask_size, last_price, volume, bar_date,
                open_interest, oi_date, iv, delta, gamma, theta, vega, rho, captured_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            payload,
        )
        conn.commit()
    return len(payload)


def backfill_open_interest(
    observations: list[tuple[str, str, int]], *, db_path: Path = CHAIN_DB_PATH
) -> int:
    """Attach open interest to the session it actually describes.

    The vendor publishes open interest one session in arrears — a sweep run after
    Friday's close sees Thursday's OI (measured live 2026-07-25: ``open_interest_date``
    was 2026-07-23 the day after the 07-24 session). Rather than storing that number
    against the wrong day, each run writes it back onto the row for ``oi_date``.
    Today's rows therefore land with a NULL open interest and are completed by
    tomorrow's run, which makes the newest session's OI legitimately absent rather
    than quietly wrong.

    ``observations`` is ``(occ, oi_date, open_interest)``.
    """
    if not observations:
        return 0
    with _connect(db_path) as conn:
        cur = conn.executemany(
            "UPDATE chain_snapshots SET open_interest = ?, oi_date = ? "
            "WHERE occ = ? AND session = ? AND open_interest IS NULL",
            [(oi, oi_date, occ, oi_date) for occ, oi_date, oi in observations],
        )
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def backfill_iv_daily_oi(
    oi_date: str, totals: dict[str, tuple[int, int]], *, db_path: Path = CHAIN_DB_PATH
) -> int:
    """Attach whole-board open-interest totals to the session they describe.

    Same one-session lag as :func:`backfill_open_interest`. Totals are summed over
    the FULL contract list rather than read back out of ``chain_snapshots``, which
    is banded — a banded sum would understate the chain's real open interest.

    ``totals`` is ``{underlying: (call_oi, put_oi)}``.
    """
    if not totals:
        return 0
    with _connect(db_path) as conn:
        cur = conn.executemany(
            "UPDATE iv_daily SET call_oi = ?, put_oi = ? WHERE session = ? AND underlying = ?",
            [(c, p, oi_date, sym) for sym, (c, p) in sorted(totals.items())],
        )
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def write_iv_daily(rows: list[dict], *, db_path: Path = CHAIN_DB_PATH) -> int:
    """Insert one session's per-underlying IV rows (the post-close reading)."""
    return _write_iv_rows("iv_daily", rows, db_path)


def write_iv_preclose(rows: list[dict], *, db_path: Path = CHAIN_DB_PATH) -> int:
    """Insert one session's per-underlying IV rows read ~15 min BEFORE the close."""
    return _write_iv_rows("iv_preclose", rows, db_path)


def _write_iv_rows(table: str, rows: list[dict], db_path: Path) -> int:
    if not rows:
        return 0
    stamp = now_iso()
    payload = [
        (
            r["session"], r["underlying"], r.get("spot"), r.get("atm_iv_30d"),
            r.get("atm_iv_front"), r.get("front_dte"), r.get("hv_ewma"), r.get("vrp"),
            r.get("call_volume") or 0, r.get("put_volume") or 0,
            r.get("call_oi") or 0, r.get("put_oi") or 0,
            r.get("contracts_observed") or 0, r.get("contracts_stored") or 0,
            r.get("bars_current") or 0, stamp, r.get("tier") or "deep",
        )
        for r in rows
    ]
    with _connect(db_path) as conn:
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {table} (
                session, underlying, spot, atm_iv_30d, atm_iv_front, front_dte,
                hv_ewma, vrp, call_volume, put_volume, call_oi, put_oi,
                contracts_observed, contracts_stored, bars_current, captured_at, tier
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            payload,
        )
        conn.commit()
    return len(payload)


def start_run(session: str, *, db_path: Path = CHAIN_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sampler_runs (session, started_at) VALUES (?, ?)",
            (session, now_iso()),
        )
        conn.commit()


def finish_run(
    session: str,
    *,
    attempted: int,
    ok: int,
    contracts: int,
    oi_backfilled: int,
    failures: list[str],
    db_path: Path = CHAIN_DB_PATH,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE sampler_runs SET finished_at = ?, symbols_attempted = ?,
                   symbols_ok = ?, contracts_written = ?, oi_backfilled = ?, failures = ?
             WHERE session = ?
            """,
            (
                now_iso(), attempted, ok, contracts, oi_backfilled,
                json.dumps(sorted(failures)) if failures else None,
                session,
            ),
        )
        conn.commit()
