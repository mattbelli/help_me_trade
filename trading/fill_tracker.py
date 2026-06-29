"""Poll-based reconciliation of submitted orders against broker status."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from brokers.factory import get_broker, get_broker_for_account
from brokers.market_stream import market_hub
from db.base import DEFAULT_DB_PATH
from db.order_executions import (
    list_stale_pending_paper_executions,
    list_submitted_executions,
    update_execution_status,
)
from trading.position_ledger import record_session_node_managed_fill, record_strategy_fill

logger = logging.getLogger(__name__)

_PENDING_MIN_AGE_SECONDS = 30
_PENDING_ORPHAN_REJECT_SECONDS = 120

_TERMINAL_STATUSES = frozenset(
    {"filled", "partially_filled", "canceled", "expired", "rejected"}
)


def _map_broker_status(broker_status: str) -> str | None:
    normalized = broker_status.strip().lower()
    if normalized in _TERMINAL_STATUSES:
        return normalized
    return None


def _execution_age_seconds(created_at: str) -> float:
    created = datetime.fromisoformat(created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created).total_seconds()


async def reconcile_pending_executions(
    broker_name: str,
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Recover paper rows stuck at pending after broker POST (orphan window)."""
    executions = list_stale_pending_paper_executions(
        broker_name,
        min_age_seconds=_PENDING_MIN_AGE_SECONDS,
        account_id=account_id,
        db_path=db_path,
    )
    if not executions:
        return

    adapter = (
        get_broker_for_account(account_id)
        if account_id is not None
        else get_broker(broker_name)
    )
    for row in executions:
        execution_id = row["id"]
        try:
            order = await adapter.aget_order_by_client_id(execution_id)
        except Exception:
            logger.exception(
                "Failed to lookup order by client_order_id for execution %s",
                execution_id,
            )
            continue

        if order is not None:
            now = datetime.now(UTC).isoformat()
            update_execution_status(
                execution_id,
                "submitted",
                broker_order_id=order.id,
                submitted_at=now,
                db_path=db_path,
            )
            continue

        if _execution_age_seconds(row["created_at"]) >= _PENDING_ORPHAN_REJECT_SECONDS:
            update_execution_status(
                execution_id,
                "rejected",
                risk_checks_json={"reason": "orphan: not found at broker"},
                db_path=db_path,
            )


async def reconcile_open_executions(
    broker_name: str,
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Poll broker for submitted executions and update local status on change."""
    executions = list_submitted_executions(
        broker_name, account_id=account_id, db_path=db_path
    )
    if not executions:
        return

    adapter = (
        get_broker_for_account(account_id)
        if account_id is not None
        else get_broker(broker_name)
    )
    for row in executions:
        execution_id = row["id"]
        broker_order_id = row.get("broker_order_id")
        if not broker_order_id:
            logger.warning(
                "Execution %s has no broker_order_id — skipping fill check",
                execution_id,
            )
            continue
        try:
            order = await adapter.aget_order(broker_order_id)
        except Exception:
            logger.exception(
                "Failed to fetch order %s for execution %s",
                broker_order_id,
                execution_id,
            )
            continue

        new_status = _map_broker_status(order.status)
        if new_status is None or new_status == row.get("status"):
            # Unchanged since last reconcile — don't re-write the row, re-record the
            # managed fill, or re-broadcast a "fill" event. (partially_filled now stays
            # in the reconcile pool until it reaches a terminal status, so without this
            # guard a lingering partial would re-fire every tick.)
            continue

        update_execution_status(execution_id, new_status, db_path=db_path)

        # Record strategy attribution into the managed-position ledger so the exit
        # engine knows whose rules apply. Bookkeeping only — fail-closed inside.
        row_dict = dict(row)
        record_strategy_fill(
            row_dict,
            filled_qty=order.filled_qty,
            status=new_status,
            db_path=db_path,
        )
        record_session_node_managed_fill(
            row_dict,
            filled_qty=order.filled_qty,
            status=new_status,
            db_path=db_path,
        )

        try:
            payload: dict[str, object] = {
                "type": "fill",
                "execution_id": execution_id,
                "status": new_status,
                "symbol": row["symbol"],
                "filled_qty": order.filled_qty,
            }
            if account_id is not None:
                payload["account_id"] = account_id
            await market_hub.broadcast_alert(payload)
        except Exception:
            logger.exception(
                "Failed to broadcast fill alert for execution %s", execution_id
            )
