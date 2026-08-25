"""Manual desk verbs — the cockpit's order surface (ticket / chart / DOM).

Every order still flows through the one chokepoint: place = propose→approve
(the same machinery as a phone approval, compressed into one admin-gated
call), cancel/replace act only on OUR execution rows via the paper adapter,
and a replace is cancel + a FULL fresh chokepoint submit — never a broker
PATCH, so the RiskEngine re-validates every leg.

Hard refusals owned here (the chokepoint's gates all still apply after):
  * protective stops — and the whole stop/trailing family — are the stop
    guard's / founder's property: never cancelable or replaceable from here;
  * orders with NO execution row were not placed by the chokepoint and are
    NEVER touched (a hand-placed broker order is reported, not cancelled);
  * book-driver orders (entries AND trims) belong to the book's own
    order-change proposals;
  * a replace/flatten only re-submits after the broker CONFIRMS the old
    order(s) died clean AND the snapshot has been re-polled with the dead
    ids dropped — submitting into a snapshot that still lists a cancelled
    order deterministically blocks the sell gate (measured in review).

Every verb leaves an inbox record (kind="execution", push=False) so "who
cancelled that, and when" always has an answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.order_executions import (
    get_execution,
    get_execution_by_broker_order,
    list_open_book_entries,
    update_execution_status,
)
from db.paper_orders import get_proposal
from imports.option_symbol import parse_occ_symbol, parse_option_symbol
from signals.notify import notify
from trading.execution_service import (
    _refresh_snapshot,
    _verb_gate,
    _verb_probe,
)
from trading.proposals import approve_proposal, create_phone_proposal

logger = logging.getLogger(__name__)

# A cancel is async at the broker (204 = accepted). We poll the order to a
# terminal state before any replace re-submits; past this window the desk
# reports cancel_pending and the replace does NOT happen.
_CANCEL_POLL_ATTEMPTS = 8
_CANCEL_POLL_DELAY_S = 0.25

# Broker order states that mean "this order can no longer execute".
# ⚠ done_for_day is NOT here: a GTC order parked at done_for_day reworks next
# session (fill_tracker pins this) — treating it as cancelled would let a
# replace double the order. Matches _CANCEL_CONFIRMED_STATUSES.
_TERMINAL_STATES = {"canceled", "cancelled", "expired", "rejected"}

MANUAL_PROPOSAL_TTL_SECONDS = 120

# Idempotency: a double-click / client retry with the same request id returns
# the first outcome instead of minting a second order. In-process is enough —
# the app is single-writer by design (trading/single_writer.py).
_IDEMPOTENCY_TTL_S = 60.0
_recent_requests: dict[str, tuple[float, dict[str, Any]]] = {}


def _idempotent_hit(request_id: str | None) -> dict[str, Any] | None:
    if not request_id:
        return None
    now = time.monotonic()
    for key, (ts, _r) in list(_recent_requests.items()):
        if now - ts > _IDEMPOTENCY_TTL_S:
            _recent_requests.pop(key, None)
    hit = _recent_requests.get(request_id)
    return dict(hit[1]) if hit else None


def _idempotent_store(request_id: str | None, result: dict[str, Any]) -> None:
    if request_id:
        _recent_requests[request_id] = (time.monotonic(), dict(result))


def _refusal(reason: str, status: str = "rejected") -> dict[str, Any]:
    return {"status": status, "reason": reason}


def _is_stop_family(order_type: Any) -> bool:
    ot = str(order_type or "").lower()
    return ot.startswith("stop") or "trail" in ot


def _is_protective_stop(row: dict[str, Any]) -> bool:
    proposal_id = str(row.get("proposal_id") or "")
    return _is_stop_family(row.get("order_type")) or proposal_id.startswith("stop|")


def _is_book_order(row: dict[str, Any], *, db_path: Path) -> bool:
    """A book-driver order — entry OR trim. Entries are recognized by the
    entry-scoped query; trims (sells) by the minting proposal's source."""
    entries = list_open_book_entries(
        str(row.get("symbol") or "").upper(),
        broker=str(row.get("broker") or ""),
        account_id=row.get("account_id"),
        db_path=db_path,
    )
    if any(e.get("id") == row.get("id") for e in entries):
        return True
    proposal_id = row.get("proposal_id")
    if proposal_id:
        proposal = get_proposal(str(proposal_id), db_path=db_path)
        source = str((proposal or {}).get("source") or "")
        if source.startswith("book_") or source == "book":
            return True
    return False


async def _notify_verb(
    *,
    title: str,
    body: str,
    account_id: str | None,
    ref_id: str,
    db_path: Path,
) -> None:
    try:
        await notify(
            kind="execution",
            title=title,
            body=body,
            severity="info",
            account_id=account_id,
            ref_type="execution",
            ref_id=ref_id,
            push=False,
            db_path=db_path,
        )
    except Exception:
        logger.exception("manual verb notify failed (%s)", title)


async def place_manual_order(
    args: dict[str, Any],
    *,
    decided_by: str = "desk:manual",
    request_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Propose + approve in one motion. The proposal row and decision journal
    entries are real — the desk's orders audit exactly like phone approvals."""
    prior = _idempotent_hit(request_id)
    if prior is not None:
        return {**prior, "idempotent_replay": True}
    row = await create_phone_proposal(
        dict(args),
        source="manual",
        score=False,
        announce=False,
        ttl_seconds=MANUAL_PROPOSAL_TTL_SECONDS,
        db_path=db_path,
    )
    if "error" in row:
        return _refusal(str(row["error"]), status="invalid")
    result = await approve_proposal(
        str(row["id"]),
        expected_hash=row.get("payload_hash"),
        decided_by=decided_by,
        db_path=db_path,
    )
    result["proposal_id"] = row["id"]
    _idempotent_store(request_id, result)
    return result


def _working_row(
    execution_id: str, *, db_path: Path
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(row, refusal) — refusal set when the row can't be acted on.

    Accepts our execution id OR the broker's order id (the snapshot's
    open_orders carry only the broker id, and the DOM/chart act from the
    snapshot). Orders with no execution row (not placed by us) stay
    untouchable — the desk only manages orders that went through the
    chokepoint."""
    row = get_execution(execution_id, db_path=db_path)
    if row is None:
        row = get_execution_by_broker_order(execution_id, db_path=db_path)
    if row is None:
        return None, _refusal("unknown execution id", status="not_found")
    if row.get("status") not in ("submitted", "partially_filled"):
        return None, _refusal(
            f"order is {row.get('status')} — only working orders can be changed",
            status="not_working",
        )
    if not row.get("broker_order_id"):
        return None, _refusal("order has no broker id yet", status="not_working")
    if _is_protective_stop(row):
        return None, _refusal(
            "protective stops belong to the stop guard — move the stop instead"
        )
    return row, None


def _gate_for_row(row: dict[str, Any]) -> tuple[Any | None, dict[str, Any] | None]:
    probe = _verb_probe(
        str(row["symbol"]).upper(),
        broker=str(row["broker"]),
        account_id=row.get("account_id"),
        proposal_id=str(row.get("proposal_id") or "manual"),
    )
    try:
        adapter, refusal = _verb_gate(probe)
    except Exception as exc:
        logger.exception("manual verb: adapter resolution failed")
        return None, _refusal(f"broker unavailable: {exc}", status="failed")
    if refusal is not None:
        return None, _refusal(refusal)
    return adapter, None


def _probe_for_row(row: dict[str, Any]) -> Any:
    return _verb_probe(
        str(row["symbol"]).upper(),
        broker=str(row["broker"]),
        account_id=row.get("account_id"),
        proposal_id=str(row.get("proposal_id") or "manual"),
    )


async def cancel_manual_order(
    execution_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    row, refusal = _working_row(execution_id, db_path=db_path)
    if refusal is not None:
        return refusal
    assert row is not None
    if _is_book_order(row, db_path=db_path):
        return _refusal(
            "working book orders belong to the book driver — use the desk's "
            "order-change proposal"
        )
    adapter, gate_refusal = _gate_for_row(row)
    if gate_refusal is not None:
        return gate_refusal
    try:
        await adapter.acancel_order(str(row["broker_order_id"]))
    except Exception as exc:
        logger.exception("manual cancel: broker refused %s", row["broker_order_id"])
        return _refusal(f"broker refused the cancel: {exc}", status="failed")
    # The row stays `submitted` on purpose: the fill tracker's reconcile
    # observes the broker's terminal state (including a partial fill that
    # landed as the cancel went in) the ordinary way.
    await _notify_verb(
        title=f"Desk cancel — {row['symbol']}",
        body=(
            f"cancel sent for {row['side']} {row['qty']} {row['symbol']}"
            f" (exec {row['id']})"
        ),
        account_id=row.get("account_id"),
        ref_id=str(row["id"]),
        db_path=db_path,
    )
    return {
        "status": "cancel_sent",
        "execution_id": row["id"],
        "symbol": row["symbol"],
    }


async def _await_clean_cancel(
    adapter: Any, broker_order_id: str, *, deadline_s: float | None = None
) -> tuple[str, Decimal]:
    """(state, filled_qty) after the cancel settles — 'canceled' | 'filled' |
    'partially_filled' | 'pending'."""
    filled = Decimal("0")
    attempts = _CANCEL_POLL_ATTEMPTS
    started = time.monotonic()
    for _ in range(attempts):
        if deadline_s is not None and time.monotonic() - started > deadline_s:
            break
        try:
            order = await adapter.aget_order(broker_order_id)
        except Exception:
            logger.warning(
                "replace: order lookup failed for %s", broker_order_id, exc_info=True
            )
            await asyncio.sleep(_CANCEL_POLL_DELAY_S)
            continue
        status = str(getattr(order, "status", "") or "").lower()
        try:
            filled = Decimal(str(getattr(order, "filled_qty", 0) or 0))
        except (InvalidOperation, ValueError):
            filled = Decimal("0")
        if status == "filled":
            return "filled", filled
        if status in _TERMINAL_STATES:
            return ("partially_filled" if filled > 0 else "canceled"), filled
        await asyncio.sleep(_CANCEL_POLL_DELAY_S)
    return "pending", filled


def _stored_terms(row: dict[str, Any], *, db_path: Path) -> tuple[str, bool]:
    """(time_in_force, extended_hours) recovered from the minting proposal —
    the execution row does not store them, and a replace must never silently
    change an order's character (a GTC coming back as day dies at the close)."""
    proposal_id = row.get("proposal_id")
    if proposal_id:
        proposal = get_proposal(str(proposal_id), db_path=db_path)
        stored = (proposal or {}).get("proposal") or {}
        return (
            str(stored.get("time_in_force") or "day"),
            bool(stored.get("extended_hours")),
        )
    return "day", False


async def replace_manual_order(
    execution_id: str,
    *,
    limit_price: Decimal,
    request_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Move a working LIMIT order to a new price: broker cancel → confirm the
    old order died clean → re-poll the snapshot with the dead id dropped →
    fresh chokepoint submit at the new price (same qty, same TIF/ext terms).

    The new submit re-runs the entire RiskEngine, so a replace can be blocked
    even though the original stood — the reason is returned verbatim.
    """
    prior = _idempotent_hit(request_id)
    if prior is not None:
        return {**prior, "idempotent_replay": True}
    if not limit_price.is_finite() or limit_price <= 0:
        return _refusal("limit_price must be a positive number", status="invalid")
    row, refusal = _working_row(execution_id, db_path=db_path)
    if refusal is not None:
        return refusal
    assert row is not None
    if row.get("order_type") != "limit":
        return _refusal("only working limit orders can be replaced")
    if _is_book_order(row, db_path=db_path):
        return _refusal(
            "working book orders belong to the book driver — use the desk's "
            "order-change proposal"
        )
    adapter, gate_refusal = _gate_for_row(row)
    if gate_refusal is not None:
        return gate_refusal

    broker_order_id = str(row["broker_order_id"])
    try:
        await adapter.acancel_order(broker_order_id)
    except Exception as exc:
        logger.exception("manual replace: broker refused cancel %s", broker_order_id)
        return _refusal(f"broker refused the cancel: {exc}", status="failed")

    state, filled = await _await_clean_cancel(adapter, broker_order_id)
    if state == "filled":
        return _refusal(
            "order filled before the cancel reached the venue — nothing replaced",
            status="filled_before_replace",
        )
    if state == "partially_filled":
        # Part of the order is a real position now; the fill tracker records
        # it. Re-submitting the remainder is a NEW decision the desk makes
        # deliberately, not something a replace does behind the founder's back.
        return _refusal(
            f"cancel landed after a partial fill ({filled}) — place the "
            "remainder as a new order if still wanted",
            status="partial_fill_no_replace",
        )
    if state == "pending":
        return _refusal(
            "cancel not confirmed by the broker yet — order untouched, retry "
            "or cancel outright",
            status="cancel_pending",
        )

    # Clean zero-fill cancel, confirmed against the broker directly — safe to
    # terminalize our row so the inflight sums release before the re-submit.
    update_execution_status(str(row["id"]), "canceled", db_path=db_path)

    # The stored snapshot still lists the dead order, and the sell gate reads
    # pending sells from it — without this refresh a full-size sell replace is
    # deterministically blocked AFTER the original was destroyed (measured).
    refreshed = await _refresh_snapshot(
        adapter,
        _probe_for_row(row),
        db_path=db_path,
        gone_order_ids=frozenset({broker_order_id}),
    )
    if not refreshed:
        return _refusal(
            "old order cancelled, but the account snapshot could not be "
            "refreshed — place the new order manually",
            status="cancelled_no_replace",
        )

    tif, ext = _stored_terms(row, db_path=db_path)
    args: dict[str, Any] = {
        "symbol": row["occ_symbol"] or row["symbol"],
        "side": row["side"],
        "qty": row["qty"],
        "order_type": "limit",
        "limit_price": str(limit_price),
        "account_id": row.get("account_id"),
        "time_in_force": tif,
        "extended_hours": ext,
    }
    result = await place_manual_order(
        args, decided_by="desk:replace", db_path=db_path
    )
    result["replaced_execution_id"] = row["id"]
    await _notify_verb(
        title=f"Desk replace — {row['symbol']}",
        body=(
            f"{row['side']} {row['qty']} {row['symbol']}: "
            f"{row.get('limit_price')} → {limit_price} "
            f"({result.get('status')})"
        ),
        account_id=row.get("account_id"),
        ref_id=str(row["id"]),
        db_path=db_path,
    )
    _idempotent_store(request_id, result)
    return result


async def close_manual_position(
    symbol: str,
    *,
    account_id: str | None,
    request_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Flatten: cancel the symbol's working desk orders, re-poll, then market
    sell the held long.

    Never touched: the stop/trailing family (the chokepoint's cancel-then-sell
    release path retires OUR protective stops atomically with the exit; a
    hand-placed stop is the founder's), book-driver orders, and any order with
    no execution row (not ours to cancel — reported back instead)."""
    from db.sqlite import get_latest_snapshot

    prior = _idempotent_hit(request_id)
    if prior is not None:
        return {**prior, "idempotent_replay": True}

    symbol = symbol.strip().upper()
    if not symbol:
        return _refusal("symbol is required", status="invalid")
    if parse_option_symbol(symbol) or parse_occ_symbol(symbol):
        return _refusal(
            "flatten covers equities only — close option positions from the "
            "chain with a limit order",
            status="invalid",
        )

    from db.accounts import (
        account_is_execution_capable,
        default_execution_account,
        get_account,
    )

    account = (
        get_account(account_id, db_path=db_path)
        if account_id
        else default_execution_account(db_path=db_path)
    )
    if account is None:
        return _refusal("no execution-capable account", status="invalid")
    account_id = str(account["id"])
    if not account_is_execution_capable(account_id, db_path=db_path):
        return _refusal(
            f"account {account_id!r} is not execution-capable", status="invalid"
        )
    broker = str(account["broker"]).strip().lower()

    snapshot = get_latest_snapshot(broker, account_id=account_id, db_path=db_path)
    if snapshot is None:
        return _refusal("no account snapshot", status="failed")

    held = Decimal("0")
    for pos in snapshot.get("positions") or []:
        if str(pos.get("symbol") or "").upper() == symbol and pos.get("side") == "long":
            try:
                held = Decimal(str(pos.get("qty")))
            except (InvalidOperation, ValueError):
                held = Decimal("0")
            break
    if held <= 0:
        return _refusal(f"no held long position for {symbol}", status="nothing_to_close")

    # Partition the symbol's working orders: ours-and-cancelable vs untouchable.
    cancelable: list[dict[str, Any]] = []  # {broker_order_id, row}
    untouchable: list[dict[str, Any]] = []  # {order_id, reason}
    adapter: Any = None
    for order in snapshot.get("open_orders") or []:
        if str(order.get("symbol") or "").upper() != symbol:
            continue
        broker_order_id = str(order.get("id") or "")
        if not broker_order_id:
            continue
        if _is_stop_family(order.get("order_type")):
            continue  # stop family is never flatten's business
        row = get_execution_by_broker_order(broker_order_id, db_path=db_path)
        if row is None:
            untouchable.append(
                {"order_id": broker_order_id, "reason": "placed outside the app"}
            )
            continue
        if _is_protective_stop(row):
            continue
        if _is_book_order(row, db_path=db_path):
            untouchable.append(
                {"order_id": broker_order_id, "reason": "book-driver order"}
            )
            continue
        if adapter is None:
            adapter, gate_refusal = _gate_for_row(row)
            if gate_refusal is not None:
                return gate_refusal
        cancelable.append({"broker_order_id": broker_order_id, "row": row})

    if adapter is None:
        # No cancels needed — still need the gate before the close submit's
        # own path runs (place_manual_order re-checks everything anyway).
        pass

    cancelled: list[str] = []
    cancel_failed: list[dict[str, Any]] = []
    confirmed_gone: set[str] = set()
    if cancelable:
        # Fire all cancels, then poll them against one shared deadline —
        # sequential 2s-per-order polling turns a 10-order flatten into a
        # ~20s HTTP request.
        results = await asyncio.gather(
            *(
                adapter.acancel_order(c["broker_order_id"])
                for c in cancelable
            ),
            return_exceptions=True,
        )
        deadline = _CANCEL_POLL_ATTEMPTS * _CANCEL_POLL_DELAY_S
        for c, res in zip(cancelable, results):
            broker_order_id = c["broker_order_id"]
            if isinstance(res, Exception):
                logger.error(
                    "flatten: cancel failed for %s: %s", broker_order_id, res
                )
                cancel_failed.append(
                    {"order_id": broker_order_id, "reason": str(res)}
                )
                continue
            cancelled.append(broker_order_id)
            state, _fill = await _await_clean_cancel(
                adapter, broker_order_id, deadline_s=deadline
            )
            if state == "canceled":
                confirmed_gone.add(broker_order_id)
                update_execution_status(
                    str(c["row"]["id"]), "canceled", db_path=db_path
                )

        # Re-poll so the sell gate stops reserving qty for the dead orders.
        await _refresh_snapshot(
            adapter,
            _probe_for_row(cancelable[0]["row"]),
            db_path=db_path,
            gone_order_ids=frozenset(confirmed_gone),
        )

    # "10.0" → "10": keep whole-share quantities integral for the whole-share
    # gate and the audit trail.
    if held == held.to_integral_value():
        held = held.to_integral_value()
    result = await place_manual_order(
        {
            "symbol": symbol,
            "side": "sell",
            "qty": str(held),
            "order_type": "market",
            "account_id": account_id,
        },
        decided_by="desk:flatten",
        db_path=db_path,
    )
    # A flatten that already cancelled orders must never surface as a bare
    # HTTP error — the route maps `invalid` to 400, which would drop the
    # record of what was destroyed.
    if result.get("status") in ("invalid", "not_found") and cancelled:
        result = {"status": "failed", "reason": result.get("reason")}
    result["cancelled_orders"] = cancelled
    if cancel_failed:
        result["cancel_failed"] = cancel_failed
    if untouchable:
        result["untouched_orders"] = untouchable
    await _notify_verb(
        title=f"Desk flatten — {symbol}",
        body=(
            f"close {held} {symbol}: {result.get('status')}"
            + (f"; cancelled {len(cancelled)} working" if cancelled else "")
            + (
                f"; {len(untouchable)} order(s) left (not ours)"
                if untouchable
                else ""
            )
        ),
        account_id=account_id,
        ref_id=str(result.get("execution_id") or symbol),
        db_path=db_path,
    )
    _idempotent_store(request_id, result)
    return result
