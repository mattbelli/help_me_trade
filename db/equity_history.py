"""Equity history + window P&L from the poller's own snapshots (read-only).

Serves the mobile home screen: any paper account's (or all-paper's) equity
curve and window P&L without a broker round-trip, from the ~15s
account_snapshots / position_snapshots poller data.

The P&L decomposition is net-cash style — no cost-basis replay:

    change     = equity_end − equity_start          (account_snapshots)
    Δunreal    = unrealized_end − unrealized_start  (position_snapshots)
    realized   = change − Δunreal

order_executions fill columns are only populated from 2026-07-01 and paper
fills never reach the trade_events ledger, so subtraction from snapshot data
is the only complete paper source. Cash deposits/withdrawals would land in
"realized" — paper accounts don't move cash; an account's seed capital only
shows up in windows that predate the account (ALL).

Scope is paper (alpaca) only: Schwab accounts aren't in the poller tables and
the paper/real firewall must hold, so real ids resolve to None here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db.base import DEFAULT_DB_PATH, connect

_ET = ZoneInfo("America/New_York")

VALID_RANGES = ("1D", "3D", "1W", "1M", "1Y", "ALL")
_RANGE_DAYS = {"3D": 3, "1W": 7, "1M": 30, "1Y": 365}

# Curve resolution: buckets sized so a window yields about this many points.
_TARGET_POINTS = 240
_MIN_BUCKET_SECONDS = 60


def _range_start_utc(range_key: str, now: datetime) -> datetime | None:
    """Window start. 1D = midnight *Eastern* (the user-facing trading day),
    not a rolling 24h and not the RTH open. ALL = None (caller uses oldest)."""
    if range_key == "ALL":
        return None
    if range_key == "1D":
        et_midnight = now.astimezone(_ET).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return et_midnight.astimezone(timezone.utc)
    return now - timedelta(days=_RANGE_DAYS[range_key])


def _scope_account_ids(conn: sqlite3.Connection, account_id: str) -> list[str]:
    if account_id == "all_paper":
        rows = conn.execute(
            "SELECT id FROM trade_accounts WHERE broker = 'alpaca' AND enabled = 1"
        ).fetchall()
    elif account_id.startswith("acct_"):
        rows = conn.execute(
            "SELECT id FROM trade_accounts WHERE id = ? AND broker = 'alpaca'",
            (account_id,),
        ).fetchall()
    else:
        rows = []
    return [r["id"] for r in rows]


def _window_endpoint(
    conn: sqlite3.Connection, account_id: str, start_iso: str, *, last: bool
) -> tuple[float, float] | None:
    """(portfolio_value, unrealized_total) at the account's first/last poll in
    the window; None when the account has no polls there."""
    order = "DESC" if last else "ASC"
    row = conn.execute(
        f"""
        SELECT p.id AS poll_id, a.portfolio_value AS pv
        FROM poll_runs p
        JOIN account_snapshots a ON a.poll_run_id = p.id
        WHERE p.account_id = ? AND p.captured_at >= ?
        ORDER BY p.captured_at {order}
        LIMIT 1
        """,
        (account_id, start_iso),
    ).fetchone()
    if row is None:
        return None
    upl = conn.execute(
        "SELECT COALESCE(SUM(unrealized_pl), 0) AS upl"
        " FROM position_snapshots WHERE poll_run_id = ?",
        (row["poll_id"],),
    ).fetchone()
    return float(row["pv"]), float(upl["upl"])


def _bucketed_points(
    conn: sqlite3.Connection,
    account_ids: list[str],
    start_iso: str,
    bucket_seconds: int,
) -> list[dict[str, float]]:
    """Summed equity curve, one point per time bucket.

    Inner query averages each account's samples per bucket; the outer sum only
    keeps buckets where EVERY scoped account reported, so a poller gap (or an
    account that didn't exist yet) reads as a gap in the curve rather than a
    fake equity drop.
    """
    placeholders = ",".join("?" for _ in account_ids)
    rows = conn.execute(
        f"""
        SELECT bucket, SUM(avg_pv) AS equity, COUNT(*) AS n_accounts
        FROM (
            SELECT CAST(strftime('%s', p.captured_at) / ? AS INTEGER) AS bucket,
                   p.account_id,
                   AVG(a.portfolio_value) AS avg_pv
            FROM poll_runs p
            JOIN account_snapshots a ON a.poll_run_id = p.id
            WHERE p.account_id IN ({placeholders}) AND p.captured_at >= ?
            GROUP BY bucket, p.account_id
        )
        GROUP BY bucket
        HAVING n_accounts = ?
        ORDER BY bucket
        """,
        (bucket_seconds, *account_ids, start_iso, len(account_ids)),
    ).fetchall()
    return [
        {
            "timestamp": row["bucket"] * bucket_seconds + bucket_seconds // 2,
            "equity": round(float(row["equity"]), 2),
        }
        for row in rows
    ]


def equity_history(
    account_id: str,
    range_key: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Equity curve + window P&L for `acct_*` (paper) or `all_paper`.

    Returns None for unknown/non-paper scopes. Point shape matches the broker
    portfolio-history contract (timestamp/equity/profit_loss/profit_loss_pct)
    so clients render both from one type.
    """
    if range_key not in VALID_RANGES:
        raise ValueError(f"invalid range: {range_key}")
    now = now or datetime.now(timezone.utc)

    with connect(db_path) as conn:
        ids = _scope_account_ids(conn, account_id)
        if not ids:
            return None

        start = _range_start_utc(range_key, now)
        if start is None:
            placeholders = ",".join("?" for _ in ids)
            row = conn.execute(
                f"SELECT MIN(captured_at) AS oldest FROM poll_runs"
                f" WHERE account_id IN ({placeholders})",
                ids,
            ).fetchone()
            if row is None or not row["oldest"]:
                return _empty(account_id, range_key)
            start = datetime.fromisoformat(row["oldest"])
        start_iso = start.astimezone(timezone.utc).isoformat()

        span = max((now - start).total_seconds(), 1.0)
        bucket_seconds = max(_MIN_BUCKET_SECONDS, int(span / _TARGET_POINTS))
        points = _bucketed_points(conn, ids, start_iso, bucket_seconds)

        firsts = []
        lasts = []
        for aid in ids:
            first = _window_endpoint(conn, aid, start_iso, last=False)
            if first is None:
                # Account has no polls in the window (e.g. created mid-window
                # for ALL, or poller was down) — it contributes nothing.
                continue
            firsts.append(first)
            lasts.append(_window_endpoint(conn, aid, start_iso, last=True))

    if not firsts:
        return _empty(account_id, range_key)

    start_equity = sum(pv for pv, _ in firsts)
    end_equity = sum(pv for pv, _ in lasts)
    upl_start = sum(upl for _, upl in firsts)
    upl_end = sum(upl for _, upl in lasts)
    change = end_equity - start_equity
    unrealized_change = upl_end - upl_start

    for p in points:
        p["profit_loss"] = round(p["equity"] - start_equity, 2)
        p["profit_loss_pct"] = (
            round((p["equity"] - start_equity) / start_equity * 100, 4)
            if start_equity
            else 0.0
        )

    return {
        "range": range_key,
        "account_id": account_id,
        "points": points,
        "start_equity": round(start_equity, 2),
        "end_equity": round(end_equity, 2),
        "change": round(change, 2),
        "unrealized_change": round(unrealized_change, 2),
        "realized": round(change - unrealized_change, 2),
        "source": "snapshots",
    }


def _empty(account_id: str, range_key: str) -> dict[str, Any]:
    return {
        "range": range_key,
        "account_id": account_id,
        "points": [],
        "start_equity": 0.0,
        "end_equity": 0.0,
        "change": 0.0,
        "unrealized_change": 0.0,
        "realized": 0.0,
        "source": "snapshots",
    }
