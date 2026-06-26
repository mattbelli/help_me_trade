"""Report generation — reuses existing signal/compute helpers (no duplicate math)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from config.settings import short_selling_allowed
from db.base import DEFAULT_DB_PATH
from db.reports import save_report
from db.strategies import get_strategy
from signals.session_node_strategy import compute_session_levels
from trading.market_hours import _ET

SESSION_NODE_STRATEGY_ID = "session_node_spy"


def _format_levels_line(levels: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for lv in sorted(levels, key=lambda x: float(x["price"]), reverse=True):
        name = str(lv["name"])
        price = float(lv["price"])
        label = f"{name} {price:.2f}"
        if name == "POC":
            label += " ★"
        parts.append(label)
    return " / ".join(parts)


def _format_strategy_line(*, strategy_enabled: bool, shorts_on: bool) -> str:
    armed = "armed" if strategy_enabled else "disarmed"
    mode = "long+short" if shorts_on else "long-only"
    return f"Session-Node: {armed} · {mode}"


async def generate_morning_levels_report(
    symbol: str = "SPY",
    *,
    db_path: Path | None = None,
    et_date: date | None = None,
) -> dict[str, Any] | None:
    """Build and store a morning levels report from the latest completed session."""
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    sym = symbol.strip().upper()
    levels = await compute_session_levels(sym, db_path=resolved)
    if levels is None:
        return None

    strat = get_strategy(SESSION_NODE_STRATEGY_ID, db_path=resolved)
    strategy_enabled = bool(strat and strat.get("enabled"))
    shorts_on = short_selling_allowed()

    if et_date is None:
        et_date = datetime.now(_ET).date()

    levels_line = _format_levels_line(levels["levels"])
    strategy_line = _format_strategy_line(
        strategy_enabled=strategy_enabled,
        shorts_on=shorts_on,
    )
    body = f"{levels_line}\n{strategy_line}"
    title = f"Morning Levels · {sym}"

    payload: dict[str, Any] = {
        **levels,
        "strategy_enabled": strategy_enabled,
        "shorts_on": shorts_on,
        "et_date": et_date.isoformat(),
    }
    return save_report(
        "morning_levels",
        sym,
        title,
        body,
        payload,
        db_path=resolved,
    )
