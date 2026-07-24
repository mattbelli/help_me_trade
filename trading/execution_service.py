"""Sole broker write chokepoint — dry_run logs; paper places Alpaca orders; live guarded."""

from __future__ import annotations

import asyncio
import logging
import weakref
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from brokers.alpaca import AlpacaError
from brokers.base import PlaceOrderRequest
from brokers.factory import get_broker, get_broker_for_account
from config.settings import is_kill_switch_active, trading_config
from db.base import DEFAULT_DB_PATH
from db.order_executions import create_execution_record, update_execution_status
from signals.notify import notify
from trading.models import ExecutionResult, OrderIntent, RiskRejectedError
from trading.risk_engine import RiskEngine

logger = logging.getLogger(__name__)

# Per-event-loop lock: validate reads in-flight/daily notional sums, then (after
# passing) submit writes this order's notional_usd. Without serialization, two
# concurrent submits can both read sums that exclude each other and pass the cap.
_submit_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Lock
] = weakref.WeakKeyDictionary()


def _submit_lock() -> asyncio.Lock:
    # SINGLE-PROCESS ASSUMPTION: prod runs one uvicorn worker (SERVER_SETUP.md),
    # so one loop covers every in-app submit caller. A second process (e.g.
    # tools/fire_test_entry.py, or a future --workers>1) gets its own lock and
    # reintroduces the cross-process race — don't add workers without a
    # DB-level reservation.
    loop = asyncio.get_running_loop()
    lock = _submit_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _submit_locks[loop] = lock
    return lock


class ExecutionService:
    @staticmethod
    async def submit(
        intent: OrderIntent,
        *,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> ExecutionResult:
        execution_id = f"exec_{uuid4().hex[:12]}"
        mode = trading_config()["execution_mode"]

        # 1. Kill switch (defense in depth)
        if is_kill_switch_active():
            create_execution_record(
                execution_id=execution_id,
                proposal_id=intent.proposal_id,
                session_id=intent.session_id,
                symbol=intent.symbol,
                side=intent.side,
                qty=intent.qty,
                order_type=intent.order_type,
                limit_price=intent.limit_price,
                execution_mode=mode,
                status="rejected",
                broker=intent.broker,
                account_id=intent.account_id,
                risk_checks_json={"kill_switch": "fail: kill switch active"},
                db_path=db_path,
            )
            raise RiskRejectedError("kill switch active")

        # 2. Pending audit row BEFORE validation or any branch
        create_execution_record(
            execution_id=execution_id,
            proposal_id=intent.proposal_id,
            session_id=intent.session_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
            execution_mode=mode,
            status="pending",
            broker=intent.broker,
            account_id=intent.account_id,
            risk_checks_json={},
            db_path=db_path,
        )

        try:
            async with _submit_lock():
                risk = await RiskEngine.validate(intent, db_path=db_path)
                if not risk.passed:
                    reason = risk.blocked_reason or "risk check failed"
                    update_execution_status(
                        execution_id,
                        "rejected",
                        risk_checks_json=risk.checks,
                        db_path=db_path,
                    )
                    raise RiskRejectedError(reason)

                update_execution_status(
                    execution_id,
                    "pending",
                    risk_checks_json=risk.checks,
                    notional_usd=risk.risk_notional,
                    short_open=1 if risk.is_short_open else 0,
                    db_path=db_path,
                )

            dry_run_log: str | None = None
            status: str
            broker_order_id: str | None = None

            if mode == "dry_run":
                dry_run_log = (
                    f"DRY RUN — would place: {intent.side.upper()} "
                    f"{intent.qty} {intent.symbol} {intent.order_type}"
                )
                status = "dry_run"
                update_execution_status(
                    execution_id,
                    status,
                    db_path=db_path,
                )
            elif mode == "live":
                reason = "live execution not enabled (Increment 3)"
                update_execution_status(
                    execution_id,
                    "rejected",
                    db_path=db_path,
                )
                raise RiskRejectedError(reason)
            elif mode == "paper":
                adapter = (
                    get_broker_for_account(intent.account_id)
                    if intent.account_id
                    else get_broker(intent.broker)
                )
                paper_ok = getattr(adapter, "is_paper_endpoint", None)
                if paper_ok is not True:
                    paper_checks = {
                        "paper_endpoint": (
                            "fail: resolved adapter is not a paper endpoint"
                        )
                    }
                    update_execution_status(
                        execution_id,
                        "rejected",
                        risk_checks_json=paper_checks,
                        clear_notional=True,
                        db_path=db_path,
                    )
                    try:
                        await notify(
                            kind="risk_block",
                            title="Paper mode blocked — live endpoint detected",
                            body=(
                                f"Execution {execution_id} rejected: resolved broker "
                                "adapter is not a paper endpoint (config drift toward live)"
                            ),
                            severity="critical",
                            account_id=intent.account_id,
                            ref_type="execution",
                            ref_id=execution_id,
                            push=True,
                            db_path=db_path,
                        )
                    except Exception:
                        logger.exception("paper endpoint block notify failed")
                    raise RiskRejectedError(
                        "resolved adapter is not a paper endpoint"
                    )

                if intent.side == "sell":
                    # RiskEngine flagged whether this sell closes a long or opens
                    # a short — tell the broker so a short opens correctly.
                    position_intent = (
                        "sell_to_open" if risk.is_short_open else "sell_to_close"
                    )
                else:
                    position_intent = "buy_to_open"
                req = PlaceOrderRequest(
                    client_order_id=execution_id,
                    symbol=intent.occ_symbol or intent.symbol,
                    side=intent.side,
                    qty=str(intent.qty),
                    order_type=intent.order_type,
                    limit_price=(
                        str(intent.limit_price)
                        if intent.limit_price is not None
                        else None
                    ),
                    time_in_force=intent.time_in_force,
                    position_intent=position_intent,
                    stop_price=(
                        str(intent.stop_price)
                        if intent.stop_price is not None
                        else None
                    ),
                    extended_hours=intent.extended_hours,
                )
                try:
                    resp = await adapter.aplace_order(req)
                except AlpacaError as exc:
                    update_execution_status(
                        execution_id,
                        "rejected",
                        risk_checks_json={"broker_error": str(exc)},
                        db_path=db_path,
                    )
                    raise RiskRejectedError(str(exc)) from exc

                broker_order_id = resp.broker_order_id
                status = "submitted"
                now = datetime.now(UTC).isoformat()
                update_execution_status(
                    execution_id,
                    status,
                    broker_order_id=broker_order_id,
                    submitted_at=now,
                    db_path=db_path,
                )
            else:
                reason = f"unknown execution mode: {mode}"
                update_execution_status(
                    execution_id,
                    "rejected",
                    db_path=db_path,
                )
                raise RiskRejectedError(reason)

            try:
                # Display-only broadcast (cockpit notification) — NOT part of the
                # execution/risk decision. Enriched with qty/mode + strategy attribution
                # so the web can render "BUY 1 AAPL · dip_buy [paper]".
                alert: dict[str, str | None] = {
                    "type": "execution",
                    "execution_id": execution_id,
                    "status": status,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "qty": str(intent.qty),
                    "execution_mode": mode,
                }
                pid = intent.proposal_id or ""
                if pid.startswith("auto_entry|"):
                    parts = pid.split("|")
                    if len(parts) >= 2:
                        alert["strategy_id"] = parts[1]
                if broker_order_id is not None:
                    alert["broker_order_id"] = broker_order_id
                await notify(
                    kind="execution",
                    title=f"{intent.side.upper()} {intent.qty} {intent.symbol} — {status}",
                    body=f"Execution {execution_id} [{mode}]",
                    severity="info",
                    strategy_id=alert.get("strategy_id"),
                    ref_type="execution",
                    ref_id=execution_id,
                    action_url=f"/executions/{execution_id}",
                    data=dict(alert),
                    push=False,
                    db_path=db_path,
                )
            except Exception:
                logger.exception("Failed to broadcast execution alert")

            return ExecutionResult(
                execution_id=execution_id,
                broker_order_id=broker_order_id,
                execution_mode=mode,
                status=status,
                dry_run_log=dry_run_log,
            )
        except RiskRejectedError:
            raise
        except Exception as exc:
            update_execution_status(
                execution_id,
                "error",
                risk_checks_json={"error": str(exc)},
                db_path=db_path,
            )
            raise
