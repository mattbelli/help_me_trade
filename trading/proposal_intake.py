"""Single intake seam for agent trade proposals — all decision sources converge here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from math import floor
from pathlib import Path
from typing import Any

from config.settings import is_kill_switch_active, primary_broker, trading_config
from db.accounts import default_execution_account, get_account
from db.base import DEFAULT_DB_PATH, now_iso
from db.order_executions import recent_open_buy_exists
from db.paper_orders import list_pending_proposals
from db.sqlite import get_latest_snapshot
from signals.notify import notify
from trading.execution_service import ExecutionService
from trading.market_hours import is_market_open
from trading.models import RiskRejectedError
from trading.order_proposal import build_order_proposal_from_args, order_intent_from_fields
from trading.proposals import create_phone_proposal
from trading.risk_engine import RiskEngine, _live_market_price

logger = logging.getLogger(__name__)

AGENT_PROPOSAL_TTL_SECONDS = 1800
_BUYING_POWER_BUFFER = Decimal("0.95")
# Alpaca fractional orders: qty to 9 decimal places, $1 minimum notional.
_FRACTIONAL_QUANTUM = Decimal("0.000000001")
_MIN_FRACTIONAL_NOTIONAL = Decimal("1")


def _quantize_fractional(qty: Decimal) -> Decimal:
    """Round a fractional share qty DOWN to Alpaca's 9-dp precision (never over-deploy)."""
    return qty.quantize(_FRACTIONAL_QUANTUM, rounding=ROUND_DOWN)
# Auto-entry double-submit window: a symbol just auto-entered isn't yet "held" (snapshot
# lag) nor a pending phone proposal, so re-scans must skip it for longer than a scan cycle
# + snapshot refresh. 15 min comfortably covers both; once filled, the `held` set takes over.
_AUTO_ENTRY_DEDUP_SECONDS = 900


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
    account_id: str | None = None
    # Size as a fractional share qty to deploy the exact notional (set for
    # percent-of-equity strategies). Off by default → whole-share floor, as before.
    allow_fractional: bool = False
    # "moo" (book nightly cycles): the proposal is created AFTER HOURS for the
    # next opening auction — skips the market-open gate, sizes off the last
    # close, expires at the next session close, and approval submits opg
    # before the ~9:28 ET cutoff. Everything else keeps "immediate" semantics.
    entry_style: str = "immediate"
    # Analyst's stated stop — stored on the proposal; the stop guard places it
    # as a resting GTC stop sell once the entry fills.
    stop_price: Decimal | None = None


async def _size_candidate(
    candidate: TradeCandidate,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Shared gating + sizing for propose and auto-entry paths."""
    if is_kill_switch_active():
        return {"status": "kill_switch"}

    if not is_market_open() and candidate.entry_style != "moo":
        return {"status": "market_closed"}

    symbol = candidate.symbol.strip().upper()

    if candidate.account_id:
        account = get_account(candidate.account_id, db_path=db_path)
        if (
            account is None
            or not account.get("enabled")
            or not account.get("execution_capable")
        ):
            return {"status": "invalid_account"}
    else:
        account = default_execution_account(db_path=db_path)

    resolved_account_id = account["id"] if account else None
    broker = account["broker"] if account else primary_broker()

    price = await _live_market_price(symbol)
    if (price is None or price <= 0) and candidate.entry_style == "moo":
        # After-hours book sizing: the fill is the NEXT opening auction, so the
        # last session close is the honest notional→qty basis. Whole-share
        # flooring below absorbs the small open-gap sizing error; RiskEngine
        # re-prices at approval with the same reference rule.
        from trading.risk_engine import _offhours_reference_price

        price = await _offhours_reference_price(symbol)
    if price is None or price <= 0:
        return {"status": "no_price"}

    raw_qty = candidate.notional_usd / price
    snapshot = get_latest_snapshot(
        broker, account_id=resolved_account_id, db_path=db_path
    )
    if snapshot is not None:
        buying_power = Decimal(str(snapshot["account"]["buying_power"]))
        raw_qty = min(raw_qty, buying_power * _BUYING_POWER_BUFFER / price)

    qty_arg: int | str
    if candidate.allow_fractional:
        # Deploy the exact notional as a fractional qty — no whole-share floor. This is
        # the point of percent-of-equity sizing: 100% of the sized dollars invested, and
        # names priced above the per-name budget stay reachable (a $6k stock on a $3k
        # order = ~0.46 shares). Alpaca fractional = market + day TIF, which is what
        # ExecutionService submits; allow_fractional exempts NO risk throttle.
        qty = _quantize_fractional(raw_qty)
        if qty * price < _MIN_FRACTIONAL_NOTIONAL:
            return {"status": "too_small"}
        # A whole-number result wires as an int ("2"), not "2.000000000".
        qty_arg = str(int(qty)) if qty == qty.to_integral_value() else str(qty)
    else:
        qty = Decimal(floor(raw_qty))
        if qty < 1:
            return {"status": "too_small"}
        qty_arg = int(qty)

    args: dict[str, Any] = {
        "symbol": symbol,
        "side": candidate.side,
        "qty": qty_arg,
        "order_type": candidate.order_type,
    }
    if candidate.allow_fractional:
        args["allow_fractional"] = True
    if candidate.limit_price is not None:
        args["limit_price"] = candidate.limit_price
    if candidate.entry_style == "moo":
        args["entry_style"] = "moo"
    if candidate.stop_price is not None:
        args["stop_price"] = candidate.stop_price
    if resolved_account_id is not None:
        args["account_id"] = resolved_account_id

    return {
        "status": "ok",
        "args": args,
        "broker": broker,
        "account_id": resolved_account_id,
        "price": str(price),  # live ref price at propose (Decision Journal R basis)
    }


def _register_idea_shadow(
    candidate: TradeCandidate,
    *,
    entry_ref_price: Any,
    user_action: str,
    source_ref: dict[str, Any],
    db_path: Path,
) -> None:
    """Fail-soft calibration-ledger registration (idea engine Phase 1).

    The ledger is downstream of trading: any failure here is logged and
    counted, and must never propagate into the propose/execute path. Only
    buy entries register — sells reduce a held long (exits are not ideas).
    """
    try:
        if str(candidate.side or "").lower() != "buy" or entry_ref_price is None:
            return
        from config.idea_engine import STRATEGY_HORIZONS
        from db.idea_outcomes import register_idea

        source = str(candidate.source or "")
        if source.startswith("strategy:"):
            origin = source
            family = source.split(":", 1)[1] or "unknown"
        elif candidate.rule_id:
            origin = f"scan:{candidate.rule_id}"
            family = "rule_alert"
        else:
            origin = source or "unknown"
            family = "unknown"
        idea_id = register_idea(
            origin=origin,
            signal_family=family,
            symbol=str(candidate.symbol).upper(),
            direction="long",
            entry_zone=None,
            stop=None,
            targets=None,
            thesis=str(candidate.rationale or ""),
            grading_basis="horizon_return",
            entry_ref_price=str(entry_ref_price),
            plan_horizon=STRATEGY_HORIZONS.get(family, "21d"),
            user_action=user_action,
            source_ref=source_ref,
            db_path=db_path,
        )
        try:
            from db.lane_stats import bump

            # register_idea returns None when the row was deduped (same idea
            # still open) or rejected by a validation wall — counting that as
            # "registered" hid dropped samples from the meters (GPT-5.6 audit
            # finding 8, 2026-07-17).
            if idea_id is not None:
                bump("path_a_register", "registered", db_path=db_path)
            else:
                bump("path_a_register", "deduped_or_rejected", db_path=db_path)
        except Exception:
            pass
    except Exception:
        logger.warning("idea-ledger registration failed (fail-soft)", exc_info=True)
        try:
            from db.lane_stats import bump

            bump("path_a_register", "failures", db_path=db_path)
        except Exception:
            pass


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

        sized = await _size_candidate(candidate, db_path=db_path)
        if sized.get("status") != "ok":
            return sized

        args = sized["args"]
        symbol = args["symbol"]
        resolved_account_id = sized.get("account_id")

        if candidate.account_id is not None:
            pending = list_pending_proposals(
                account_id=resolved_account_id, db_path=db_path
            )
        else:
            pending = list_pending_proposals(db_path=db_path)
        # Book (moo) cycles are exempt from the pending-count cap: the analyst
        # sizes and shapes its own nightly slate (founder 07-23), each book has
        # its own account, and _propose_advanced bounds the slate at 10 — the
        # cap would silently drop a 4th pick (adversarial review M3-lifecycle).
        # The same-symbol dedup below still applies to every path.
        if candidate.entry_style != "moo":
            max_pending = cfg.get("max_pending_agent_proposals", 3)
            if len(pending) >= max_pending:
                return {"status": "max_pending"}
        if any(p["symbol"].upper() == symbol for p in pending):
            return {"status": "duplicate"}

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

        if candidate.entry_style == "moo":
            # A nightly book proposal must survive until the founder's morning
            # approval and stays actionable (market-at-approval) until that
            # session ends — expire it at the next regular-session close.
            from trading.market_hours import next_session_close

            ttl_seconds = max(
                AGENT_PROPOSAL_TTL_SECONDS,
                int((next_session_close() - datetime.now(UTC)).total_seconds()),
            )
        else:
            ttl_seconds = AGENT_PROPOSAL_TTL_SECONDS
        row = await create_phone_proposal(
            args,
            source=candidate.source,
            rule_id=candidate.rule_id,
            thesis=candidate.rationale,
            entry_ref_price=sized.get("price"),
            ttl_seconds=ttl_seconds,
            db_path=db_path,
        )
        if "error" in row:
            return {"status": "invalid", "reason": row["error"]}

        _register_idea_shadow(
            candidate,
            entry_ref_price=sized.get("price"),
            user_action="skipped",
            source_ref={"kind": "proposal", "id": str(row["id"])},
            db_path=db_path,
        )
        return {"status": "proposed", "id": row["id"]}
    except Exception:
        logger.exception(
            "propose_trade failed for %s (%s)",
            candidate.symbol,
            candidate.source,
        )
        return {"status": "error"}


async def auto_execute_entry(
    candidate: TradeCandidate,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Auto-execute a strategy entry through the execution chokepoint (paper-fenced upstream)."""
    try:
        # Defense in depth: the scan's dispatch fence already gates on this, but this
        # function must be self-protecting — auto-entry never runs outside paper/dry_run.
        if trading_config()["execution_mode"] not in ("dry_run", "paper"):
            logger.error("auto_execute_entry blocked — execution_mode is not paper/dry_run")
            return {"status": "blocked_live"}

        # The synthetic proposal_id encodes the strategy for fill attribution; refuse to
        # auto-submit anything we can't attribute (else the fill opens no managed position
        # and the exit engine is blind to it).
        if not candidate.source.startswith("strategy:"):
            logger.error("auto_execute_entry called with non-strategy source %r", candidate.source)
            return {"status": "error", "reason": "non-strategy source"}
        strategy_id = candidate.source.split("strategy:", 1)[1]
        if not strategy_id:
            return {"status": "error", "reason": "empty strategy id"}

        sized = await _size_candidate(candidate, db_path=db_path)
        if sized.get("status") != "ok":
            return sized
        args = sized["args"]

        # Double-submit guard: skip if a non-failed buy for this symbol+account is already
        # in flight (or just filled but not yet reconciled into the snapshot/held set).
        if recent_open_buy_exists(
            args["symbol"],
            account_id=sized.get("account_id"),
            within_seconds=_AUTO_ENTRY_DEDUP_SECONDS,
            db_path=db_path,
        ):
            return {"status": "duplicate"}

        built = build_order_proposal_from_args(args, db_path=db_path)
        if "error" in built:
            return {"status": "invalid", "reason": built["error"]}
        intent = order_intent_from_fields(
            built["intent_fields"],
            proposal_id=f"auto_entry|{strategy_id}|{now_iso()}",
            session_id=None,
            db_path=db_path,
        )
        result = await ExecutionService.submit(intent, db_path=db_path)
        _register_idea_shadow(
            candidate,
            entry_ref_price=sized.get("price"),
            user_action="auto",
            source_ref={"kind": "execution", "id": str(result.execution_id)},
            db_path=db_path,
        )
        await notify(
            kind="execution",
            title=f"BUY {args['qty']} {args['symbol']} @ mkt [{result.execution_mode}]",
            body=f"Auto-entry: {strategy_id} bought {args['qty']} {args['symbol']}",
            severity="success",
            strategy_id=strategy_id,
            ref_type="execution",
            ref_id=result.execution_id,
            action_url=f"/executions/{result.execution_id}",
            data={
                "symbol": args["symbol"],
                "side": "buy",
                "qty": float(args["qty"]),
                "strategy_id": strategy_id,
                "execution_id": result.execution_id,
            },
            push=True,
            db_path=db_path,
        )
        return {"status": "executed", "execution_id": result.execution_id}
    except RiskRejectedError as exc:
        return {"status": "risk_blocked", "reason": str(exc)}
    except Exception:
        logger.exception("auto_execute_entry failed for %s", candidate.symbol)
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
