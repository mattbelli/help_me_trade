"""Schwab account-value history from recorded broker liquidation values.

Transactions alone cannot reconstruct account equity while holdings change in
value. Use the daily Schwab snapshots (including held days), and the latest
successful account snapshot for today's endpoint. No historical values are
invented before recording began, and incomplete aggregate days are omitted.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db.base import DEFAULT_DB_PATH, connect
from db.schwab_history import init_schwab_history_tables

_ET = ZoneInfo("America/New_York")
_RANGE_DAYS = {"3D": 3, "1W": 7, "1M": 30, "1Y": 365}


def schwab_equity_curve(
    account_id: str | None = None, *, days: int = 30, range_key: str | None = None
) -> dict[str, Any]:
    # Account refresh already has its own single-flight cache. The history read
    # is local and cheap; a second curve cache only delays new balance captures.
    return _compute(account_id, days, range_key=range_key)


def _compute(
    account_id: str | None,
    days: int,
    *,
    range_key: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    today: date | None = None,
) -> dict[str, Any]:
    from brokers.schwab_accounts import get_schwab_accounts_sync

    live = get_schwab_accounts_sync()
    result: dict[str, Any] = {
        "authenticated": bool(live.get("authenticated")),
        "source": "schwab_snapshots",
        "stale": bool(live.get("stale") or live.get("error")),
        "history_start": None,
        "history_end": None,
        "points": [],
    }
    if not result["authenticated"]:
        return result
    accounts = live.get("accounts", [])
    if account_id:
        accounts = [a for a in accounts if a.get("id") == account_id]
    if not accounts:
        return result

    ids = {a["id"] for a in accounts}
    end = today or datetime.now(_ET).date()
    init_schwab_history_tables(db_path)
    placeholders = ",".join("?" for _ in ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT snapshot_date, account_id, liquidation_value FROM schwab_account_daily "
            f"WHERE account_id IN ({placeholders}) AND snapshot_date <= ? "
            "ORDER BY snapshot_date",
            (*sorted(ids), end.isoformat()),
        ).fetchall()

    by_day: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for row in rows:
        if row["liquidation_value"] is not None:
            by_day[row["snapshot_date"]][row["account_id"]] = Decimal(row["liquidation_value"])

    current = {a["id"]: Decimal(str(a["liquidation_value"])) for a in accounts
               if a.get("liquidation_value") is not None}
    result["current_liquidation"] = float(sum(current.values()))
    # A stale cache must never be relabelled as a new day's observation.
    fetched_at = live.get("fetched_at")
    fetched_today = fetched_at and datetime.fromtimestamp(fetched_at, _ET).date() == end
    if not result["stale"] and fetched_today and current.keys() == ids:
        by_day[end.isoformat()] = current
    points = [
        {"date": day, "equity": round(float(sum(values.values())), 2)}
        for day, values in sorted(by_day.items())
        if values.keys() == ids
    ]
    if points:
        result["history_start"] = points[0]["date"]
        result["history_end"] = points[-1]["date"]
    if range_key == "1D":
        # Daily observations, not an invented intraday series: previous close
        # to latest balance. Keep the last available pair over weekends too.
        points = points[-2:]
    elif range_key != "ALL":
        start = (end - timedelta(days=_RANGE_DAYS.get(range_key, days))).isoformat()
        points = [p for p in points if p["date"] >= start]
    result["points"] = points
    return result
