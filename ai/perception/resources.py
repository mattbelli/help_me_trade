"""Generic resource query — registry + dispatcher (read-only)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config.settings import get_watchlists_state
from db.base import DEFAULT_DB_PATH
from db.chart_features import list_price_alerts
from db.paper_orders import get_proposal, list_pending_proposals
from db.reports import list_reports
from db.strategies import list_strategies

logger = logging.getLogger(__name__)

_MAX_ROWS = 50
_PREVIEW_LEN = 200

ReaderFn = Callable[..., list[dict[str, Any]]]


def _cap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows[:_MAX_ROWS]


def _read_watchlists(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path
    state = get_watchlists_state()
    rows = [
        {
            "id": wl["id"],
            "name": wl["name"],
            "symbols": wl.get("symbols", []),
        }
        for wl in state.get("lists", [])
        if isinstance(wl, dict) and wl.get("id") and wl.get("name") is not None
    ]
    name_filter = filters.get("name")
    if name_filter:
        name_lower = str(name_filter).lower()
        rows = [r for r in rows if str(r["name"]).lower() == name_lower]
    return _cap_rows(rows)


def _read_strategies(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    enabled_only = bool(filters.get("enabled_only"))
    rows = [
        {
            "id": s["id"],
            "name": s["name"],
            "enabled": s["enabled"],
            "account_id": s.get("account_id"),
            "notional_usd": s["notional_usd"],
            "exit_mode": s["exit_mode"],
            "universe": s["universe"],
            "max_concurrent_positions": s.get("max_concurrent_positions"),
            "max_deployed_usd": s.get("max_deployed_usd"),
        }
        for s in list_strategies(enabled_only=enabled_only, db_path=db_path)
    ]
    return _cap_rows(rows)


def _read_price_alerts(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path
    symbol = filters.get("symbol")
    sym = str(symbol).strip().upper() if symbol else None
    active_only = filters.get("active_only", True)
    if not isinstance(active_only, bool):
        active_only = bool(active_only)
    rows = [
        {
            "id": a.get("id"),
            "symbol": a.get("symbol"),
            "price": a.get("price"),
            "direction": a.get("direction"),
            "repeat": bool(a.get("repeat")),
            "note": a.get("note") or "",
            "triggered": bool(a.get("triggered")),
        }
        for a in list_price_alerts(symbol=sym, active_only=active_only)
    ]
    return _cap_rows(rows)


def _read_proposals(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    account_id = filters.get("account_id")
    acct = str(account_id) if account_id else None
    rows: list[dict[str, Any]] = []
    for summary in list_pending_proposals(account_id=acct, db_path=db_path):
        full = get_proposal(summary["id"], db_path=db_path) or {}
        rows.append(
            {
                "id": full.get("id") or summary["id"],
                "symbol": full.get("symbol") or summary["symbol"],
                "side": full.get("side") or summary["side"],
                "qty": full.get("qty") or summary["qty"],
                "order_type": full.get("order_type"),
                "status": full.get("status"),
                "source": full.get("source"),
                "created_at": full.get("created_at"),
            }
        )
    return _cap_rows(rows)


def _trim_preview(text: str | None) -> str | None:
    if not text:
        return None
    trimmed = text.strip()
    if len(trimmed) <= _PREVIEW_LEN:
        return trimmed
    return trimmed[:_PREVIEW_LEN].rstrip() + "…"


def _read_reports(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    limit = min(max(int(filters.get("limit", 10)), 1), _MAX_ROWS)
    rows: list[dict[str, Any]] = []
    for report in list_reports(limit=limit, db_path=db_path):
        row: dict[str, Any] = {
            "id": report["id"],
            "kind": report["kind"],
            "title": report["title"],
            "symbol": report.get("symbol"),
            "created_at": report.get("generated_at"),
        }
        preview = _trim_preview(report.get("body"))
        if preview:
            row["preview"] = preview
        rows.append(row)
    return rows


# ⚠️ MAINTENANCE CONTRACT: when you add a new data source / feature to the app that the
# copilot should be able to read, ADD AN ENTRY HERE (description + filters + a fail-soft
# reader reusing the existing function). That one line is what makes the agent able to
# query it — the perception layer does NOT auto-discover new data. (Same idea as
# get_context for per-symbol fields.) See References/PERCEPTION_LAYER.md.
RESOURCES: dict[str, dict[str, Any]] = {
    "watchlists": {
        "description": "User watchlists and their symbols.",
        "filters": {"name": "optional — filter by watchlist name"},
        "read": _read_watchlists,
    },
    "strategies": {
        "description": "Configured trading strategies and enablement.",
        "filters": {"enabled_only": "optional bool — only enabled strategies"},
        "read": _read_strategies,
    },
    "price_alerts": {
        "description": "Chart price alerts (active or triggered).",
        "filters": {
            "symbol": "optional — filter by ticker",
            "active_only": "optional bool — default true",
        },
        "read": _read_price_alerts,
    },
    "proposals": {
        "description": "Paper order proposals pending phone approval.",
        "filters": {"account_id": "optional — filter by paper account id"},
        "read": _read_proposals,
    },
    "reports": {
        "description": "Stored reports (morning levels, research, etc.).",
        "filters": {"limit": "optional int — default 10, max 50"},
        "read": _read_reports,
    },
}


def list_resources() -> dict[str, dict[str, Any]]:
    """Catalog for the manifest: {name: {description, filters}}."""
    return {
        name: {
            "description": entry["description"],
            "filters": entry["filters"],
        }
        for name, entry in RESOURCES.items()
    }


async def query(
    resource: str,
    filters: dict | None = None,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Dispatch a read-only query to a registered resource (fail-soft)."""
    key = resource.strip().lower()
    if key not in RESOURCES:
        return {
            "error": "unknown resource",
            "available": sorted(RESOURCES.keys()),
        }

    reader: ReaderFn = RESOURCES[key]["read"]
    try:
        rows = reader(filters or {}, db_path=db_path)
        return {"resource": key, "count": len(rows), "rows": rows}
    except Exception as exc:
        logger.debug("Resource read failed for %s", key, exc_info=True)
        return {"resource": key, "error": str(exc), "rows": []}
