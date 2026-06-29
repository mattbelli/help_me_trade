"""Sole broker write chokepoint — dry_run logs; paper places Alpaca orders; live guarded."""

from __future__ import annotations

import logging
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
                    time_in_force="day",
                    position_intent=position_intent,
                )
                adapter = (
                    get_broker_for_account(intent.account_id)
                    if intent.account_id
                    else get_broker(intent.broker)
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
