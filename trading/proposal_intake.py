"""Single intake seam for agent trade proposals — all decision sources converge here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from math import floor
from pathlib import Path
from typing import Any

from config.settings import is_kill_switch_active, primary_broker, trading_config
from db.base import DEFAULT_DB_PATH
from db.paper_orders import list_pending_proposals
from db.sqlite import get_latest_snapshot
from trading.market_hours import is_market_open
from trading.order_proposal import build_order_proposal_from_args, order_intent_from_fields
from trading.proposals import create_phone_proposal
from trading.risk_engine import RiskEngine, _live_market_price

logger = logging.getLogger(__name__)

AGENT_PROPOSAL_TTL_SECONDS = 1800
_BUYING_POWER_BUFFER = Decimal("0.95")


@dataclass(frozen=True)
class TradeCandidate:
    symbol: str
    side: str
    notional_usd: Decimal
    order_type: str = "market"
    limit_price: Decimal | None = None
    source: str = "unknown"
    rule_id: str | None = None
    rationale: str | None = None


async def propose_trade(
    candidate: TradeCandidate,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Convergence point: validate, size, and create a pending phone proposal."""
    try:
        cfg = trading_config()
        if not cfg.get("agent_proposals_enabled", True):
            return {"status": "disabled"}

        if is_kill_switch_active():
            return {"status": "kill_switch"}

        if not is_market_open():
            return {"status": "market_closed"}

        symbol = candidate.symbol.strip().upper()
        pending = list_pending_proposals(db_path=db_path)
        max_pending = cfg.get("max_pending_agent_proposals", 3)
        if len(pending) >= max_pending:
            return {"status": "max_pending"}
        if any(p["symbol"].upper() == symbol for p in pending):
            return {"status": "duplicate"}

        price = _live_market_price(symbol)
        if price is None or price <= 0:
            return {"status": "no_price"}

        qty = floor(candidate.notional_usd / price)
        snapshot = get_latest_snapshot(primary_broker(), db_path=db_path)
        if snapshot is not None:
            buying_power = Decimal(str(snapshot["account"]["buying_power"]))
            cap_qty = floor(buying_power * _BUYING_POWER_BUFFER / price)
            qty = min(qty, cap_qty)

        if qty < 1:
            return {"status": "too_small"}

        args: dict[str, Any] = {
            "symbol": symbol,
            "side": candidate.side,
            "qty": int(qty),
            "order_type": candidate.order_type,
        }
        if candidate.limit_price is not None:
            args["limit_price"] = candidate.limit_price

        built = build_order_proposal_from_args(args, db_path=db_path)
        if "error" in built:
            return {"status": "invalid", "reason": built["error"]}

        intent = order_intent_from_fields(
            built["intent_fields"],
            proposal_id="precheck",
            session_id=None,
            db_path=db_path,
        )
        risk = await RiskEngine.validate(intent, db_path=db_path)
        if not risk.passed:
            return {"status": "risk_blocked", "reason": risk.blocked_reason}

        row = await create_phone_proposal(
            args,
            source=candidate.source,
            rule_id=candidate.rule_id,
            ttl_seconds=AGENT_PROPOSAL_TTL_SECONDS,
            db_path=db_path,
        )
        if "error" in row:
            return {"status": "invalid", "reason": row["error"]}

        return {"status": "proposed", "id": row["id"]}
    except Exception:
        logger.exception(
            "propose_trade failed for %s (%s)",
            candidate.symbol,
            candidate.source,
        )
        return {"status": "error"}


async def propose_from_scan_fire(
    item: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Producer: translate a fired scan rule into propose_trade when action.type is trade."""
    action = item.get("action") or {}
    if action.get("type") != "trade":
        return None

    try:
        symbol_raw = action.get("symbol")
        side_raw = action.get("side")
        notional_raw = action.get("notional_usd")
        if not symbol_raw or not side_raw or notional_raw is None:
            logger.warning(
                "Scan trade rule %s missing required action fields: %s",
                item.get("rule_id"),
                action,
            )
            return None

        notional = Decimal(str(notional_raw))
        if notional <= 0:
            logger.warning(
                "Scan trade rule %s has invalid notional_usd: %s",
                item.get("rule_id"),
                notional_raw,
            )
            return None

        limit_raw = action.get("limit_price")
        limit_price = Decimal(str(limit_raw)) if limit_raw is not None else None

        candidate = TradeCandidate(
            symbol=str(symbol_raw).strip().upper(),
            side=str(side_raw).strip().lower(),
            notional_usd=notional,
            order_type=str(action.get("order_type", "market")).strip().lower(),
            limit_price=limit_price,
            source="agent_scan",
            rule_id=item.get("rule_id"),
            rationale=item.get("rule_name"),
        )
        return await propose_trade(candidate, db_path=db_path)
    except Exception:
        logger.exception(
            "propose_from_scan_fire failed for rule %s",
            item.get("rule_id"),
        )
        return None
