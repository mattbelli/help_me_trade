"""Sole broker write chokepoint — dry_run in B1; paper/live guarded until B2."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from brokers.market_stream import market_hub
from config.settings import is_kill_switch_active, trading_config
from db.base import DEFAULT_DB_PATH
from db.order_executions import create_execution_record, update_execution_status
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
            elif mode in ("paper", "live"):
                reason = "paper/live execution not wired yet (B2)"
                update_execution_status(
                    execution_id,
                    "rejected",
                    db_path=db_path,
                )
                raise RiskRejectedError(reason)
            else:
                reason = f"unknown execution mode: {mode}"
                update_execution_status(
                    execution_id,
                    "rejected",
                    db_path=db_path,
                )
                raise RiskRejectedError(reason)

            try:
                await market_hub.broadcast_alert(
                    {
                        "type": "execution",
                        "execution_id": execution_id,
                        "status": status,
                        "symbol": intent.symbol,
                        "side": intent.side,
                    }
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
