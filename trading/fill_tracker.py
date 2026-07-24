"""Poll-based reconciliation of submitted orders against broker status."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from brokers.factory import get_broker, get_broker_for_account
from db.base import DEFAULT_DB_PATH
from db.order_executions import (
    list_stale_pending_paper_executions,
    list_submitted_executions,
    update_execution_status,
)
from db.paper_orders import get_proposal
from signals.notify import notify
from trading.position_ledger import (
    flush_fill_attribution_orphans,
    record_session_node_managed_fill,
    record_strategy_fill,
)
from trading.proposals import _qty_str

logger = logging.getLogger(__name__)

_PENDING_MIN_AGE_SECONDS = 30
_PENDING_ORPHAN_REJECT_SECONDS = 120

_TERMINAL_STATUSES = frozenset(
    {"filled", "partially_filled", "canceled", "expired", "rejected"}
)

# Alpaca's `done_for_day` ends a day order's SESSION, but per Alpaca's docs the
# order "will not receive further updates until the next trading day" — it is NOT
# declared final like expired/canceled (board #41 + adversarial review). So the
# mapping branches on fill progress:
#   filled_qty > 0  → "partially_filled": attribution opens the managed position
#                     for the real shares bought, the row STAYS in the reconcile
#                     pool, and the phone push waits for the true terminal status.
#   filled_qty == 0 → "expired": nothing to attribute; the resting order died at
#                     day end (the original stuck-"submitted" bug scenario).
#   EXCEPT gtc     → None: a GTC order (book limit/ext entries) parked at
#                     done_for_day reworks NEXT session — treating it as expired
#                     drops it from the reconcile pool, so a day-3 fill would be
#                     an invisible, unprotected position (entry-type review H1,
#                     2026-07-24). The row stays "submitted" and keeps polling.


async def _flush_attribution_orphans(db_path: Path) -> None:
    """Fire the founder's critical page for any unmanaged-fill orphans. Never raises."""
    try:
        await flush_fill_attribution_orphans(db_path=db_path)
    except Exception:
        logger.exception("Failed to flush fill-attribution orphan notifications")


def _map_broker_status(
    broker_status: str,
    filled_qty: object = None,
    time_in_force: str | None = None,
) -> str | None:
    normalized = broker_status.strip().lower()
    if normalized == "done_for_day":
        if _filled_qty_positive(filled_qty):
            normalized = "partially_filled"
        elif (time_in_force or "").strip().lower() == "gtc":
            return None  # still working — reworks next session
        else:
            normalized = "expired"
    if normalized in _TERMINAL_STATUSES:
        return normalized
    return None


def _price_str(price: object) -> str:
    """Wire-format money: cents-quantized Decimal string, never raw float repr."""
    try:
        return str(Decimal(str(price)).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return str(price)


def _filled_qty_positive(filled_qty: object) -> bool:
    try:
        return float(filled_qty) > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _fill_report_outcome(status: str, filled_qty: object) -> str | None:
    """Map broker terminal status to fill_report contract outcome (None = no push)."""
    if status == "partially_filled":
        return None
    if status == "filled":
        return "filled"
    partial = _filled_qty_positive(filled_qty)
    if status in {"canceled", "rejected"}:
        return "partial" if partial else "canceled"
    if status == "expired":
        return "partial" if partial else "unfilled"
    return None


def _fill_report_status_reason(
    outcome: str, *, filled_qty: object, requested_qty: object
) -> str:
    if outcome == "partial":
        return f"partial fill — {_qty_str(filled_qty)} of {_qty_str(requested_qty)}"
    if outcome == "canceled":
        return "canceled before fill"
    if outcome == "unfilled":
        return "expired unfilled"
    return ""


def _fill_report_body(
    outcome: str,
    *,
    filled_qty: object,
    avg_price: object,
) -> str:
    if outcome == "filled":
        price_part = f" @ ${avg_price}" if avg_price else ""
        return f"Filled {_qty_str(filled_qty)}{price_part}"
    if outcome == "partial":
        price_part = f" @ ${avg_price}" if avg_price else ""
        return f"Partially filled {_qty_str(filled_qty)}{price_part}"
    if outcome == "canceled":
        return "Canceled, unfilled"
    if outcome == "unfilled":
        # An expiry can hit at the OPEN (opg auction) as well as the close —
        # don't assert a session boundary the tracker can't verify here.
        return "Expired, unfilled"
    return outcome


async def _try_send_fill_report_push(
    row: dict[str, object],
    *,
    execution_id: str,
    new_status: str,
    order: object,
    account_id: str | None,
    db_path: Path,
) -> None:
    """Mobile fill_report for phone-approved proposals on terminal transitions only."""
    proposal_id = row.get("proposal_id")
    if not proposal_id:
        return
    proposal_row = get_proposal(str(proposal_id), db_path=db_path)
    # PHONE-ONLY gate: delivery=="phone" is written solely by
    # create_pending_proposal (the phone-approval flow). Web-chat propose_order
    # rows (status="recorded", delivery NULL) execute immediately with no phone
    # card — they must NOT push, per the alert-fatigue contract.
    if not proposal_row or proposal_row.get("delivery") != "phone":
        return

    filled_qty = getattr(order, "filled_qty", 0)
    outcome = _fill_report_outcome(new_status, filled_qty)
    if outcome is None:
        return

    symbol = str(row.get("symbol", ""))
    avg_price = getattr(order, "filled_avg_price", None)
    status_reason = _fill_report_status_reason(
        outcome,
        filled_qty=filled_qty,
        requested_qty=row.get("qty"),
    )
    data: dict[str, object] = {
        "type": "fill_report",
        "proposal_id": proposal_id,
        "execution_id": execution_id,
        "outcome": outcome,
        "symbol": symbol,
        "side": str(row.get("side", "")),
        "filled_qty": _qty_str(filled_qty),
        "requested_qty": _qty_str(row.get("qty")),
        # Money on an external wire: quantize to cents via Decimal — never ship
        # raw float repr noise (broker adapter parses filled_avg_price as float).
        "avg_price": "" if not avg_price else _price_str(avg_price),
    }
    if outcome != "filled":
        data["status_reason"] = status_reason

    await notify(
        kind="fill_report",
        title=f"{symbol} {outcome}",
        body=_fill_report_body(outcome, filled_qty=filled_qty, avg_price=avg_price),
        severity="info" if outcome == "filled" else "warning",
        account_id=str(account_id) if account_id else None,
        ref_type="execution",
        ref_id=execution_id,
        action_url=f"/executions/{execution_id}",
        data=data,
        push=True,
        db_path=db_path,
    )


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
    """Poll broker for submitted executions and update local status on change.

    SINGLE-POLLER ASSUMPTION: this has no cross-process guard — two processes
    reconciling the same broker/account (e.g. tools/fire_test_entry.py while the
    server's poll loop runs) can both observe a status change and double-push
    fill_report. Don't run a second reconciler against a live-polled account.
    Also: account_id=None returns ALL accounts' rows for the broker — never add
    an account-less call alongside the per-account poll tasks (double-process).
    """
    executions = list_submitted_executions(
        broker_name, account_id=account_id, db_path=db_path
    )
    if not executions:
        # Nothing to reconcile, but still page any orphan quarantined on a prior
        # tick (safety-M1 crash recovery — the durable row outlives the process).
        await _flush_attribution_orphans(db_path)
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

        new_status = _map_broker_status(
            order.status,
            getattr(order, "filled_qty", None),
            getattr(order, "time_in_force", None),
        )
        if new_status is None or new_status == row.get("status"):
            # Unchanged since last reconcile — don't re-write the row, re-record the
            # managed fill, or re-broadcast a "fill" event. (partially_filled now stays
            # in the reconcile pool until it reaches a terminal status, so without this
            # guard a lingering partial would re-fire every tick.)
            continue

        # Persist the broker's realized fill economics alongside the status (Decision
        # Journal J0). Only on a real fill — a canceled/expired order carries no meaningful
        # avg price. None-valued fields are skipped by update_execution_status.
        if new_status in {"filled", "partially_filled"}:
            update_execution_status(
                execution_id,
                new_status,
                avg_fill_price=order.filled_avg_price,
                filled_qty=order.filled_qty,
                db_path=db_path,
            )
        else:
            # Terminal canceled/expired can still carry a partial fill when the
            # order died between polls (first observation is already terminal).
            # Persist the real share count so downstream consumers — the stop
            # guard protecting a terminal-partial book entry — see the shares
            # (adversarial review M2-lifecycle, 2026-07-23).
            update_execution_status(
                execution_id,
                new_status,
                filled_qty=(
                    order.filled_qty
                    if _filled_qty_positive(getattr(order, "filled_qty", None))
                    else None
                ),
                avg_fill_price=(
                    getattr(order, "filled_avg_price", None)
                    if _filled_qty_positive(getattr(order, "filled_qty", None))
                    else None
                ),
                db_path=db_path,
            )

        # Record strategy attribution into the managed-position ledger so the exit
        # engine knows whose rules apply. Bookkeeping only — fail-closed inside.
        row_dict = dict(row)
        record_strategy_fill(
            row_dict,
            filled_qty=order.filled_qty,
            filled_avg_price=order.filled_avg_price,
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
            data: dict[str, object] = {
                "execution_id": execution_id,
                "status": new_status,
                "symbol": row["symbol"],
                "filled_qty": order.filled_qty,
            }
            if account_id is not None:
                data["account_id"] = account_id
            await notify(
                kind="fill",
                title=f"{row['symbol']} {new_status}",
                body=(
                    f"Execution {execution_id}: {order.filled_qty} shares {new_status}"
                ),
                severity="success" if new_status == "filled" else "info",
                account_id=str(account_id) if account_id else None,
                ref_type="execution",
                ref_id=execution_id,
                action_url=f"/executions/{execution_id}",
                data=data,
                push=False,
                db_path=db_path,
            )
        except Exception:
            logger.exception(
                "Failed to broadcast fill alert for execution %s", execution_id
            )

        try:
            await _try_send_fill_report_push(
                row,
                execution_id=execution_id,
                new_status=new_status,
                order=order,
                account_id=account_id,
                db_path=db_path,
            )
        except Exception:
            logger.exception(
                "Failed to send fill_report push for execution %s", execution_id
            )

    # Page the founder for any fill that could not be attributed to a managed
    # position this pass (safety-M1: an unmanaged fill must never stay silent).
    await _flush_attribution_orphans(db_path)
