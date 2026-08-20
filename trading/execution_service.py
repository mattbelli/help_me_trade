"""Sole broker write chokepoint — dry_run logs; paper places Alpaca orders; live guarded."""

from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from brokers.alpaca import AlpacaError
from brokers.base import PlaceOrderRequest
from brokers.factory import get_broker, get_broker_for_account
from config.settings import is_kill_switch_active, trading_config
from db.accounts import get_account
from db.base import DEFAULT_DB_PATH
from db.order_executions import (
    create_execution_record,
    list_open_book_entries,
    list_open_protective_stops,
    update_execution_status,
)
from db.paper_orders import set_book_entry_stop_price
from db.sqlite import get_latest_snapshot, save_snapshot
from signals.notify import notify
from trading.models import (
    ExecutionResult,
    OrderIntent,
    RiskCheckResult,
    RiskRejectedError,
)
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
    # so one loop covers every in-app submit caller. A second process gets its
    # own lock and reintroduces the cross-process race — the backend enforces
    # this at boot via trading/single_writer.py (kernel flock on the ledger;
    # --workers>1 fails loudly). Ad-hoc tools (tools/fire_test_entry.py) are
    # NOT fenced — don't add a second submitter without a DB-level reservation.
    loop = asyncio.get_running_loop()
    lock = _submit_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _submit_locks[loop] = lock
    return lock


def _adapter_for(intent: OrderIntent) -> Any:
    return (
        get_broker_for_account(intent.account_id)
        if intent.account_id
        else get_broker(intent.broker)
    )


# --- Protective-stop release ("cancel-then-sell") ----------------------------
#
# A resting protective stop is an open SELL for the whole position, so the
# RiskEngine counts it against `available` and every exit — the founder's own
# tap included — is rejected with the shares plainly sitting there. The stop is
# doing exactly its job; the position is simply locked, and until now the only
# way out was the broker's own UI. So the chokepoint takes the stop down for an
# exit and re-establishes protection immediately after:
#
#   * only stops THIS APP placed are ever cancelled (see
#     list_open_protective_stops) — a stop set by hand at the broker has no
#     execution row, is left alone, and the sell stays rejected saying so;
#   * a stop counts as released only once the broker CONFIRMS it cancelled with
#     nothing filled, so a stop that triggered mid-flight can never be mistaken
#     for shares still available to sell;
#   * whatever the exit does not sell gets a fresh stop at the same trigger, so
#     sell qty + re-stopped qty equals what the stops covered — a trim leaves the
#     remainder protected instead of bare;
#   * any failure between the cancel and a placed sell puts the original stops
#     straight back, and a restore that itself fails pages the founder.
#
# GTC sells are excluded: one can rest for days, and trading a stop for a resting
# limit would leave the position with no downside protection for all of it.

_CANCEL_CONFIRM_ATTEMPTS = 10
_CANCEL_CONFIRM_DELAY_S = 0.3
_CANCEL_CONFIRMED_STATUSES = frozenset({"canceled", "cancelled", "expired"})
# Statuses meaning the stop is already off the book — nothing to release, and
# nothing to restore afterwards either.
_STOP_ALREADY_GONE_STATUSES = _CANCEL_CONFIRMED_STATUSES | {"filled", "rejected"}


@dataclass(frozen=True)
class _ReleasedStop:
    """A protective stop the broker confirmed cancelled, kept so it can go back."""

    execution_id: str
    proposal_id: str | None
    symbol: str
    qty: Decimal
    stop_price: Decimal
    broker: str
    account_id: str | None
    # The broker's id for the cancelled order, so the re-poll can drop it if the
    # broker's order LIST has not caught up with its own cancel yet.
    broker_order_id: str


def _gone_ids(released: list[_ReleasedStop]) -> frozenset[str]:
    """Broker ids of the stops a release has confirmed cancelled."""
    return frozenset(s.broker_order_id for s in released)


def _release_is_warranted(
    intent: OrderIntent, risk: RiskCheckResult, mode: str
) -> bool:
    """Only an immediate equity exit may take a protective stop down.

    MARKET ONLY, deliberately. A resting exit (limit/GTC) can sit unfilled for a
    session or a week, and trading a stop for one leaves the position with no
    downside protection for all of that time — the correct mechanism there is a
    broker-side OCO pair, not cancel-then-sell. A market exit closes the window
    to the round-trip. This also excludes stop orders (the stop guard owns that
    lifecycle) without needing a clause of their own.

    EQUITY ONLY: for an option intent ``intent.symbol`` is the bare UNDERLYING
    (trading/order_proposal.py), so without this an option sell-to-close would
    look up — and cancel — the equity stop on the underlying, then re-stop a
    share count computed from a contract count (adversarial review H1).
    """
    return (
        mode == "paper"
        and intent.side == "sell"
        and intent.order_type == "market"
        and intent.asset_class == "us_equity"
        and risk.blocked_by_pending_sells is not None
    )


def _wire_qty(qty: Decimal) -> Decimal:
    """Whole-share quantities wire as integers ("30", never "30.0") — a decimal
    qty routes a non-fractionable symbol through Alpaca's fractional engine and
    the stop gets rejected (same rule as trading/stop_guard.py)."""
    return Decimal(int(qty)) if qty == qty.to_integral_value() else qty


async def _confirm_cancelled(adapter: Any, broker_order_id: str) -> Any | None:
    """The broker's final word on a cancelled stop, or None if we never got one.

    Polls rather than trusting the cancel acknowledgement: an order can sit in
    pending_cancel, and revalidating while it is still open would just fail the
    exit again — or, worse, let a stop that filled instead of cancelling look
    like shares we still hold. Returns the order so the caller can tell a clean
    cancel from one that filled on the way out, and say which in the page.
    """
    for _ in range(_CANCEL_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_CANCEL_CONFIRM_DELAY_S)
        try:
            order = await adapter.aget_order(broker_order_id)
        except Exception:
            logger.exception(
                "stop release: could not confirm cancel of %s", broker_order_id
            )
            return None
        status = str(order.status or "").strip().lower()
        if status in _CANCEL_CONFIRMED_STATUSES or status == "filled":
            return order
    return None


async def _release_protective_stops(
    intent: OrderIntent,
    adapter: Any,
    released: list[_ReleasedStop],
    *,
    exit_execution_id: str,
    db_path: Path,
    only_proposal_ids: frozenset[str] | None = None,
) -> None:
    """Cancel our resting protective stops for this symbol so an exit can pass.

    ``only_proposal_ids`` narrows the sweep to stops under those ``stop|<entry>``
    ids (a stop move acts on the entries it was minted for, never on one that
    appeared since); None keeps the exit path's every-stop-on-the-symbol scope.

    Appends to the CALLER's list rather than returning one: a stop is cancelled
    at the broker before its row is written, so an exception in between (the
    SQLite write is the realistic one — db/base.py documents a measured
    busy-timeout incident) must not lose the record of what is already down.
    Whatever was released is visible to submit's restore path either way
    (adversarial review C3).

    Only stops the broker CONFIRMED cancelled are appended. Anything ambiguous
    stops the sweep where it stands so the still-resting stop keeps blocking and
    the exit is rejected — but "ambiguous" is not "safe": once the cancel has
    been accepted, the stop is dying whatever we observe, so that case pages.
    """
    rows = list_open_protective_stops(
        intent.symbol,
        broker=intent.broker,
        account_id=intent.account_id,
        db_path=db_path,
    )
    for row in rows:
        if only_proposal_ids is not None and row.get("proposal_id") not in only_proposal_ids:
            continue
        broker_order_id = str(row["broker_order_id"])
        try:
            order = await adapter.aget_order(broker_order_id)
        except Exception:
            logger.exception("stop release: could not read stop %s", broker_order_id)
            return
        status = str(order.status or "").strip().lower()
        if status in _STOP_ALREADY_GONE_STATUSES:
            # Gone at the broker; the local row simply had not reconciled yet.
            # Nothing to cancel, and it must not be "restored" later either.
            continue
        if order.filled_qty:
            # A partly filled stop means the position is already smaller than the
            # snapshot says. Cancelling here would let the sell validate against
            # shares that are gone, so leave the stop up and let the rejection stand.
            logger.warning(
                "stop release: stop %s for %s is partly filled (%s) — not releasing",
                broker_order_id, intent.symbol, order.filled_qty,
            )
            return
        if order.stop_price is None:
            # We could not put this one back as it stood, so we do not take it down.
            logger.warning(
                "stop release: stop %s for %s has no trigger price — not releasing",
                broker_order_id, intent.symbol,
            )
            return
        stop = _ReleasedStop(
            execution_id=str(row["id"]),
            proposal_id=row.get("proposal_id"),
            symbol=intent.symbol,
            # The broker's resting qty, not the local row's: a row that drifted
            # from what is actually working would carry that drift straight into
            # the re-stop arithmetic (adversarial review M1).
            qty=Decimal(str(order.qty)),
            stop_price=Decimal(str(order.stop_price)),
            broker=str(row["broker"]),
            account_id=row.get("account_id"),
            broker_order_id=broker_order_id,
        )
        # Write-ahead: mark the row dead BEFORE asking the broker to kill it. A
        # crash or a failed write after a successful DELETE would otherwise leave
        # a 'submitted' row over an order that no longer exists, which reads to
        # the stop guard as "protected" and blinds it for good.
        update_execution_status(
            stop.execution_id,
            "canceled",
            risk_checks_json={"released_for": exit_execution_id},
            db_path=db_path,
        )
        try:
            await adapter.acancel_order(broker_order_id)
        except Exception:
            logger.exception("stop release: cancel failed for %s", broker_order_id)
            # The broker refused; the order is untouched, so put the row back and
            # let the normal reconcile own it.
            update_execution_status(stop.execution_id, "submitted", db_path=db_path)
            return
        final = await _confirm_cancelled(adapter, broker_order_id)
        if final is None or final.filled_qty:
            # The cancel was ACCEPTED, so the stop is not "still resting" — it is
            # either gone or it sold on the way out. Either way the shares it
            # covered are no longer protected and are not safely sellable, so the
            # row stays dead and the founder is told rather than left to find out
            # (adversarial review C2). The reason distinguishes the two, because
            # this is the text that reaches his phone.
            await _page_stop_gone(
                stop,
                qty=stop.qty,
                reason=(
                    "cancel accepted but never confirmed — stop state unknown"
                    if final is None
                    else f"the stop filled {final.filled_qty} shares as it was cancelled"
                ),
                db_path=db_path,
            )
            return
        released.append(stop)


async def _refresh_snapshot(
    adapter: Any,
    intent: OrderIntent,
    *,
    db_path: Path,
    gone_order_ids: frozenset[str] = frozenset(),
) -> bool:
    """Re-poll the broker and store it, so revalidation sees the cancelled stops.

    Returns False when nothing was stored — the stale snapshot then stands, the
    exit is rejected, and the stops go back. The account-identity check mirrors
    the poll loop (api/main.py:_poll_account): a snapshot saved under the wrong
    account would gate every later order there against somebody else's portfolio.

    ``gone_order_ids`` are orders the broker has CONFIRMED cancelled, one by one,
    through its per-order read (`_confirm_cancelled`). Alpaca's open-orders LIST
    lags that read, intermittently: measured 2026-08-18/19/20 (GOOGL, GLD, MU
    across two accounts; MU 08-19 happened to land), two list reads 455 ms apart
    both still carried a stop the per-order read had already reported
    ``canceled``, and it was gone by the next poll 9 s later — so "sleep and
    re-poll" is not a fix. RiskEngine and `_uncommitted_shares` counted the dead
    stop as a pending sell, found zero uncommitted shares, and refused BOTH the
    moved stop and the restore (the guard then re-armed the old trigger). An
    order we hold a confirmed cancel for is dropped from the snapshot: the
    per-order read is the stronger truth, and a snapshot that echoes the list's
    lag gates the next order against shares that are genuinely free.

    Residual (narrow, fails toward a page + guard re-arm, never an oversell): the
    poll loop writes snapshots without the submit lock, so a poll that lands
    inside the lag window AFTER this refresh and BEFORE the re-place validates
    can put the phantom back. Closing it means threading known-dead ids into
    RiskEngine.validate — not done.
    """
    try:
        # Stamped BEFORE the fetch: see save_snapshot's captured_at note.
        asked_at = datetime.now(UTC).isoformat()
        account, positions, orders = await adapter.afetch_snapshot()
    except Exception:
        logger.exception("stop release: snapshot refresh failed")
        return False
    if gone_order_ids:
        lagging = [o for o in orders if str(o.id) in gone_order_ids]
        if lagging:
            logger.warning(
                "stop release: broker order list still shows %d confirmed-cancelled "
                "order(s) for %s (%s) — dropped from the refreshed snapshot",
                len(lagging), intent.symbol, ", ".join(str(o.id) for o in lagging),
            )
            orders = [o for o in orders if str(o.id) not in gone_order_ids]
    account_row = (
        get_account(intent.account_id, db_path=db_path)
        if intent.account_id is not None
        else None
    ) or {}
    registered = account_row.get("broker_external_id")
    if registered and str(registered) != str(account.id):
        logger.error(
            "stop release: snapshot account mismatch for %s "
            "(registered=%s fetched=%s)",
            intent.account_id, registered, account.id,
        )
        return False
    save_snapshot(
        account,
        positions,
        orders,
        # The account's own broker, matching what the poll loop records — the
        # adapter came from get_broker_for_account, so intent.broker is only a
        # caller's label for it. With no account row the label stays EXACTLY
        # intent.broker: that is the key RiskEngine and _uncommitted_shares read
        # back with, and normalising only the write side would hide the snapshot
        # from both.
        broker=(
            str(account_row["broker"]).strip().lower()
            if account_row.get("broker")
            else intent.broker
        ),
        account_id=intent.account_id,
        captured_at=asked_at,
        db_path=db_path,
    )
    return True


async def _place_stop(
    stop: _ReleasedStop, qty: Decimal, *, db_path: Path
) -> str | None:
    """Re-place a protective stop for ``qty`` shares. Returns the execution id.

    Goes back through submit() rather than straight to the adapter — a restored
    stop is an order like any other and gets the full risk pass. Keeps the
    original ``stop|<entry>`` proposal id so the stop guard still recognises the
    entry as protected and does not place a second one.
    """
    result = await ExecutionService.submit(
        OrderIntent(
            proposal_id=stop.proposal_id,
            session_id=None,
            symbol=stop.symbol,
            side="sell",
            qty=_wire_qty(qty),
            order_type="stop",
            limit_price=None,
            stop_price=stop.stop_price,
            time_in_force="gtc",
            broker=stop.broker,
            asset_class="us_equity",
            account_id=stop.account_id,
        ),
        db_path=db_path,
    )
    return result.execution_id


async def _exit_may_be_live(adapter: Any, exit_execution_id: str) -> bool:
    """True when the 'failed' exit might actually be resting at the broker.

    brokers/alpaca.py wraps httpx.RequestError as AlpacaError, so a read timeout
    on the place POST is indistinguishable from a genuine rejection — and the
    order may well have been accepted. Stacking a full-size stop on top of a
    live full-size sell double-commits the position, so the client_order_id is
    checked before anything goes back. Unknown counts as live: the failure
    direction that pages beats the one that oversells (adversarial review H2).
    """
    lookup = getattr(adapter, "aget_order_by_client_id", None)
    if lookup is None:
        return True
    try:
        order = await lookup(exit_execution_id)
        if order is None:
            return False
        # EXISTS is not LIVE. The lookup now resolves closed orders too (it asks
        # the by-client-order-id route), so a place POST that timed out on the
        # wire but was REJECTED at the broker comes back as a real order — and
        # reading that as "the exit is resting" leaves the position bare. Only
        # the states that definitively went nowhere release the stops back;
        # anything unrecognised still counts as live (adversarial review B).
        return str(getattr(order, "status", "") or "").strip().lower() not in {
            "canceled",
            "cancelled",
            "expired",
            "rejected",
        }
    except Exception:
        logger.exception(
            "stop release: could not check whether exit %s reached the broker",
            exit_execution_id,
        )
        return True


async def _restore_protective_stops(
    released: list[_ReleasedStop],
    *,
    adapter: Any,
    exit_execution_id: str,
    place_attempted: bool,
    db_path: Path,
) -> None:
    """Put released stops back after the exit did not go through.

    ``place_attempted`` False means the order never left this process (the
    revalidation rejected it), so there is nothing that could be resting at the
    broker and the stops go straight back. Only an attempted place has to prove
    the exit is absent first — asking on the pure-rejection path would leave the
    position bare on a lookup error, and page about an exit that never existed
    (adversarial review MEDIUM-2).
    """
    exit_live = place_attempted and await _exit_may_be_live(
        adapter, exit_execution_id
    )
    for stop in released:
        if exit_live:
            await _page_stop_gone(
                stop,
                qty=stop.qty,
                reason=(
                    "the exit may be resting at the broker despite reporting "
                    "failure, so no stop was restored on top of it"
                ),
                db_path=db_path,
            )
            continue
        try:
            await _place_stop(stop, stop.qty, db_path=db_path)
        except Exception as exc:
            logger.exception(
                "stop release: FAILED to restore stop for %s", stop.symbol
            )
            await _page_stop_gone(stop, qty=stop.qty, reason=str(exc), db_path=db_path)


def _uncommitted_shares(
    intent: OrderIntent,
    *,
    exit_order_id: str | None,
    exit_qty: Decimal,
    db_path: Path,
) -> tuple[Decimal, Decimal]:
    """(shares not already committed to an open sell, current price) for the symbol.

    Read from the stored snapshot, which the caller has just refreshed from the
    broker. Advisory: it bounds what the re-stop ASKS for so a fully-committed
    position is not mistaken for unprotected shares. RiskEngine remains the
    authority on whether the resulting stop is allowed.

    The exit just placed is counted EXACTLY ONCE, whichever way the broker's
    reads land. afetch_snapshot fetches account, positions and orders as three
    independent requests, so a market exit that fills in between can be gone
    from the open orders while the position read is still pre-fill — inflating
    the budget by the exit's own size and re-stopping shares that are already
    sold (adversarial review MEDIUM-1). Absent from the open orders ⇒ subtract
    it from the position explicitly.
    """
    snapshot = (
        get_latest_snapshot(
            intent.broker, account_id=intent.account_id, db_path=db_path
        )
        or {}
    )
    held = price = Decimal("0")
    for pos in snapshot.get("positions") or []:
        if str(pos.get("symbol", "")).upper() == intent.symbol and (
            pos.get("side") == "long"
        ):
            held = Decimal(str(pos.get("qty", 0)))
            price = Decimal(str(pos.get("current_price", 0)))
            break
    pending = Decimal("0")
    exit_seen = False
    for order in snapshot.get("open_orders") or []:
        if (
            str(order.get("symbol", "")).upper() == intent.symbol
            and order.get("side") == "sell"
        ):
            if exit_order_id is not None and str(order.get("id")) == exit_order_id:
                exit_seen = True
            pending += max(
                Decimal("0"),
                Decimal(str(order.get("qty", 0)))
                - Decimal(str(order.get("filled_qty", 0))),
            )
    if not exit_seen:
        held -= exit_qty
    return max(Decimal("0"), held - pending), price


async def _restop_residual(
    released: list[_ReleasedStop],
    sell_qty: Decimal,
    *,
    budget: Decimal | None,
    price: Decimal,
    db_path: Path,
) -> Decimal:
    """Re-protect the shares the exit did NOT sell. Returns the qty re-stopped.

    Stops are consumed cheapest trigger first so the tightest protection is what
    survives a partial exit, and the shares that remain are allotted to the
    tightest triggers first.

    ``budget`` is what the broker says is genuinely uncommitted now that the exit
    is placed. The released stops' own qty is NOT a safe bound: a stop outlives
    the position it was sized for, so a 50-share stop can be resting over a
    30-share position after a founder trim, and re-stopping the "residual 20"
    would commit 50 sells against 30 shares — a resting short-to-open, the exact
    thing the sell gate exists to prevent (adversarial review C1). None means the
    broker could not be re-read: nothing is placed and a plausible residual pages
    rather than being guessed at.
    """
    remaining = sell_qty
    plan: list[tuple[_ReleasedStop, Decimal]] = []
    for stop in sorted(released, key=lambda s: s.stop_price):
        consumed = min(remaining, stop.qty)
        remaining -= consumed
        plan.append((stop, stop.qty - consumed))

    if budget is None:
        for stop, residual in plan:
            if residual > 0:
                await _page_stop_gone(
                    stop,
                    qty=residual,
                    reason="the broker could not be re-read after the exit",
                    db_path=db_path,
                )
        return Decimal("0")

    placed = Decimal("0")
    for stop, residual in sorted(
        plan, key=lambda item: item[0].stop_price, reverse=True
    ):
        allot = min(residual, budget)
        if allot <= 0:
            continue
        budget -= allot
        if price > 0 and price <= stop.stop_price:
            # The market came to the trigger while we were exiting. Re-placing
            # now would market-sell the remainder the moment the broker accepts
            # it — the same refusal the stop guard makes, which does NOT live in
            # RiskEngine and so would not catch this (adversarial review L2).
            await _page_stop_gone(
                stop,
                qty=allot,
                reason=(
                    f"{stop.symbol} is at {price}, at or through the {stop.stop_price} "
                    "trigger — re-placing would liquidate the remainder on acceptance"
                ),
                db_path=db_path,
            )
            continue
        try:
            await _place_stop(stop, allot, db_path=db_path)
            placed += allot
        except Exception as exc:
            logger.exception(
                "stop release: FAILED to re-stop %s residual for %s",
                allot, stop.symbol,
            )
            await _page_stop_gone(stop, qty=allot, reason=str(exc), db_path=db_path)
    return placed


async def _notify_stops_released(
    released: list[_ReleasedStop],
    intent: OrderIntent,
    execution_id: str,
    *,
    restopped: Decimal,
    db_path: Path,
) -> None:
    """Record that an exit changed what is protecting the position.

    Inbox, not a phone buzz — the exit itself already notifies, and this is the
    detail behind it. Reports the qty actually re-stopped, never the qty we set
    out to re-stop: a failed re-stop already pages, and an info note claiming the
    shares were covered would contradict it (adversarial review M2).
    """
    covered = sum((s.qty for s in released), Decimal("0"))
    try:
        await notify(
            kind="execution",
            title=f"Protective stop released — {intent.symbol}",
            body=(
                f"Released {len(released)} protective stop(s) covering {covered} "
                f"shares to place a {intent.qty}-share market exit; "
                + (
                    f"{restopped} shares re-stopped."
                    if restopped > 0
                    else "no shares left uncommitted, so nothing was re-stopped."
                )
            ),
            severity="info",
            account_id=intent.account_id,
            ref_type="execution",
            ref_id=execution_id,
            push=False,
            db_path=db_path,
        )
    except Exception:
        logger.exception("stop release: failed to notify for %s", intent.symbol)


async def _page_stop_gone(
    stop: _ReleasedStop,
    *,
    qty: Decimal,
    reason: str,
    db_path: Path,
    released_for: str = "to place an exit",
) -> None:
    """The position is bare and only a human can fix it — page once, loudly."""
    try:
        await notify(
            kind="risk_block",
            title=f"UNPROTECTED position: {stop.symbol}",
            body=(
                f"{stop.symbol}: the protective stop at {stop.stop_price} was "
                f"released {released_for} and could not be put back on {qty} "
                f"shares ({reason}). Place a stop manually or flatten."
            ),
            severity="critical",
            account_id=stop.account_id,
            ref_type="execution",
            ref_id=stop.execution_id,
            push=True,
            db_path=db_path,
        )
    except Exception:
        logger.exception("stop release: failed to page for %s", stop.symbol)


# --- Analyst order-management verbs (trading/book_orders.py) ------------------
#
# The nightly books can only ask; these run at the founder's approval tap and
# touch the broker through the same pieces the release path uses, in the same
# order. Both are paper-only and refuse under the kill switch. Neither is a new
# way to place an order: the only order either creates goes through submit().


def _verb_probe(
    symbol: str, *, broker: str, account_id: str | None, proposal_id: str
) -> OrderIntent:
    """The intent shape the release/refresh helpers key on (symbol, broker,
    account) — never submitted."""
    return OrderIntent(
        proposal_id=proposal_id,
        session_id=None,
        symbol=symbol,
        side="sell",
        qty=Decimal("1"),
        order_type="stop",
        limit_price=None,
        stop_price=None,
        time_in_force="gtc",
        broker=broker,
        asset_class="us_equity",
        account_id=account_id,
    )


def _verb_gate(probe: OrderIntent) -> tuple[Any | None, str | None]:
    """Shared refusals: paper mode, kill switch, paper endpoint. Returns
    (adapter, None) when clear, (None, reason) when not."""
    mode = trading_config()["execution_mode"]
    if mode != "paper":
        return None, f"order changes are paper-only (mode={mode})"
    if is_kill_switch_active():
        return None, "kill switch active"
    adapter = _adapter_for(probe)
    if getattr(adapter, "is_paper_endpoint", None) is not True:
        return None, "resolved adapter is not a paper endpoint"
    return adapter, None


def _snapshot_long(
    broker: str, account_id: str | None, symbol: str, *, db_path: Path
) -> tuple[Decimal, Decimal] | None:
    """(qty, current_price) of the long position in the stored snapshot, or None."""
    snapshot = get_latest_snapshot(broker, account_id=account_id, db_path=db_path)
    if snapshot is None:
        return None
    for pos in snapshot.get("positions") or []:
        if str(pos.get("symbol") or "").upper() != symbol or pos.get("side") != "long":
            continue
        try:
            qty = Decimal(str(pos.get("qty")))
            price = Decimal(str(pos.get("current_price")))
        except Exception:
            return None
        return (qty, price) if qty > 0 and price > 0 else None
    return None


async def move_protective_stops(
    symbol: str,
    *,
    broker: str,
    account_id: str | None,
    stop_price: Decimal,
    proposal_id: str,
    entry_proposal_ids: frozenset[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Re-trigger the protective stops THIS APP rests for ``entry_proposal_ids``
    on symbol+account at ``stop_price`` — the analyst's ``move_stop`` verb.

    Built from the release/restore pieces above, in their order: under the
    submit lock take each targeted stop down (write-ahead row, broker-CONFIRMED
    cancel), re-poll the broker so the RiskEngine sees the shares uncommitted,
    then place the new stop through submit() under the SAME ``stop|<entry>``
    proposal id, so the stop guard keeps recognising the entry as protected. A
    new stop that fails to place puts the ORIGINAL back; a restore that fails
    marks the entry's stop row ``error`` (the guard re-arms it from the stored
    price on its next pass) and pages — the same failure direction as an exit
    that fell through, plus the automatic retry.

    Only the entries the proposal was minted for are touched: a stop that
    appeared since (a new entry approved this morning) keeps its own trigger.

    Refuses outright, nothing released, when the trigger is at or above the
    current price (the broker fires such a stop as a market sell the moment it
    accepts it — the stop guard's own rule), when the position is not in the
    snapshot, or when the resting stops cover MORE shares than are held (a
    stop outlives the position it was sized for after a founder trim; re-placing
    it would be a resting short-to-open and the restore would fail the same way
    — adversarial review C1). Both checks repeat against the refreshed snapshot
    AFTER the release, and the re-place is bounded by what is genuinely
    uncommitted then. Returns counts, never assumptions:
    {status, reason?, moved, restored, lost, untouched}.
    """
    symbol = symbol.strip().upper()
    probe = _verb_probe(
        symbol, broker=broker, account_id=account_id, proposal_id=proposal_id
    )
    adapter, refusal = _verb_gate(probe)
    if refusal is not None:
        return {"status": "rejected", "reason": refusal}
    if stop_price <= 0:
        return {"status": "rejected", "reason": "stop_price must be positive"}
    stop_pids = frozenset(f"stop|{pid}" for pid in entry_proposal_ids)
    all_rows = list_open_protective_stops(
        symbol, broker=broker, account_id=account_id, db_path=db_path
    )
    rows = [r for r in all_rows if r.get("proposal_id") in stop_pids]
    if not rows:
        return {"status": "nothing_to_move", "reason": "no resting protective stop"}

    released: list[_ReleasedStop] = []
    lock = _submit_lock()
    async with lock:
        # Fresh look BEFORE touching anything: the gates below and the
        # revalidation after the release both read the stored snapshot.
        if not await _refresh_snapshot(adapter, probe, db_path=db_path):
            return {"status": "failed", "reason": "could not refresh the broker snapshot"}
        long = _snapshot_long(broker, account_id, symbol, db_path=db_path)
        if long is None:
            return {"status": "rejected", "reason": "no held position in the snapshot"}
        held, price = long
        if price <= stop_price:
            return {
                "status": "rejected",
                "reason": (
                    f"stop {stop_price} is at or above the market {price} — "
                    "it would fire as a market sell on accept"
                ),
            }
        # EVERY resting stop on the name, not just the targeted ones: the
        # re-place is bounded by what is uncommitted after the release, and an
        # untargeted stop's shares are committed — so a release is fully
        # undoable only if all stops together fit the position.
        resting_qty = sum((Decimal(str(r.get("qty") or 0)) for r in all_rows), Decimal("0"))
        if resting_qty > held:
            return {
                "status": "rejected",
                "reason": (
                    f"the resting stop covers {resting_qty} shares but only {held} are "
                    "held — cancelling it could not be undone; fix the stop by hand"
                ),
            }
        await _release_protective_stops(
            probe,
            adapter,
            released,
            exit_execution_id=proposal_id,
            db_path=db_path,
            only_proposal_ids=stop_pids,
        )
        if not released:
            # The release path logged (or paged) exactly why it stopped.
            return {
                "status": "nothing_released",
                "reason": "the resting stop could not be taken down cleanly",
                "untouched": len(rows),
            }
        if await _refresh_snapshot(
            adapter, probe, db_path=db_path, gone_order_ids=_gone_ids(released)
        ):
            budget, price = _uncommitted_shares(
                probe, exit_order_id=None, exit_qty=Decimal("0"), db_path=db_path
            )
        else:
            budget, price = Decimal("0"), Decimal("0")
    # Lock handed back: _place_stop goes through submit(), which re-takes it.
    # The position is not double-sellable in the gap — submit writes a pending
    # row before validating and RiskEngine reserves in-flight sells from those
    # rows — but it IS momentarily bare, exactly like the restore path in
    # submit's `finally`.

    moved: list[dict[str, Any]] = []
    restored: list[dict[str, Any]] = []
    lost: list[dict[str, Any]] = []
    for stop in sorted(released, key=lambda s: s.stop_price):
        allot = min(stop.qty, budget)
        budget -= max(Decimal("0"), allot)
        if Decimal("0") < allot < stop.qty:
            # The market's view of the position shrank between the gate and
            # the re-poll (a fill on the way out). The shares the new stop
            # cannot cover are bare and nothing below will cover them — say so
            # loudly rather than report the smaller stop as a clean move.
            short = stop.qty - allot
            await _page_stop_gone(
                stop,
                qty=short,
                reason=f"only {allot} of {stop.qty} shares were uncommitted after the release",
                released_for=f"to move it to {stop_price}",
                db_path=db_path,
            )
            lost.append({"qty": str(short)})
        failure: str | None
        if price <= 0:
            failure = "could not refresh the broker snapshot after the release"
        elif allot <= 0:
            failure = "no uncommitted shares left after the release"
        elif price <= stop_price:
            # The market came to the trigger during the round trip; the new
            # stop would liquidate on acceptance. Fall through to the restore
            # (the OLD trigger is still below the market by construction).
            failure = f"{symbol} is at {price}, at or through the new {stop_price} trigger"
        else:
            try:
                execution_id = await _place_stop(
                    replace(stop, stop_price=stop_price), allot, db_path=db_path
                )
                moved.append(
                    {"execution_id": execution_id, "qty": str(allot), "from": str(stop.stop_price)}
                )
                if stop.proposal_id and stop.proposal_id.startswith("stop|"):
                    set_book_entry_stop_price(
                        stop.proposal_id[len("stop|"):],
                        stop_price=str(stop_price),
                        db_path=db_path,
                    )
                continue
            except Exception as exc:
                logger.exception("stop move: new stop failed for %s", symbol)
                failure = str(exc)
        restore_qty = allot if allot > 0 else stop.qty
        try:
            execution_id = await _place_stop(stop, restore_qty, db_path=db_path)
            restored.append({"execution_id": execution_id, "qty": str(restore_qty)})
        except Exception as exc:
            logger.exception("stop move: FAILED to restore stop for %s", symbol)
            # The write-ahead 'canceled' row would read to the stop guard as
            # "protected" for good; 'error' puts the entry back in its pool so
            # it re-arms the STORED (old) price on the next tick.
            update_execution_status(
                stop.execution_id,
                "error",
                risk_checks_json={
                    "released_for": proposal_id,
                    "restore_failed": f"{failure}; restore: {exc}",
                },
                db_path=db_path,
            )
            await _page_stop_gone(
                stop,
                qty=stop.qty,
                reason=f"move to {stop_price} failed: {failure}; restore failed: {exc}",
                released_for=f"to move it to {stop_price}",
                db_path=db_path,
            )
            lost.append({"qty": str(stop.qty)})

    if moved and not restored and not lost:
        status, reason = "moved", None
    elif moved:
        status, reason = "partial", f"{len(restored)} restored, {len(lost)} lost"
    else:
        status = "failed"
        reason = (
            f"new stop refused ({len(restored)} put back at the old price"
            + (f", {len(lost)} could not be" if lost else "")
            + ")"
        )
    untouched = len(rows) - len(released)
    covered = sum((Decimal(m["qty"]) for m in moved), Decimal("0"))
    detail = (
        (f"; {len(restored)} put back at the old price" if restored else "")
        + (f"; {len(lost)} could NOT be put back (paged)" if lost else "")
        + (f"; {untouched} left untouched at the old price" if untouched else "")
        + "."
    )
    try:
        await notify(
            kind="execution",
            title=f"Stop moved — {symbol}" if moved else f"Stop move failed — {symbol}",
            body=(
                f"{symbol}: {len(moved)} protective stop(s) covering {covered} shares now "
                f"trigger at {stop_price}{detail}"
                if moved
                else f"{symbol}: the stop could not be moved to {stop_price}{detail}"
            ),
            severity="critical" if lost else "info",
            account_id=account_id,
            ref_type="proposal",
            ref_id=proposal_id,
            push=False,
            db_path=db_path,
        )
    except Exception:
        logger.exception("stop move: failed to notify for %s", symbol)
    out: dict[str, Any] = {
        "status": status,
        "moved": moved,
        "restored": restored,
        "lost": lost,
        "untouched": untouched,
    }
    if reason:
        out["reason"] = reason
    return out


async def cancel_book_entries(
    symbol: str,
    *,
    broker: str,
    account_id: str | None,
    proposal_id: str,
    execution_ids: frozenset[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Pull the working BUY entries a book placed on symbol+account — the
    analyst's ``cancel_entry`` verb — restricted to ``execution_ids``, the rows
    the proposal was minted for (an entry approved since is not its business).

    Only asks the broker to cancel; the execution rows are left ``submitted``
    on purpose. An entry is not protection, so there is no write-ahead need,
    and the fill tracker's next reconcile observes the broker's terminal
    status the ordinary way — recording a partial fill that landed as the
    cancel went in, so the stop guard then protects those shares.
    """
    symbol = symbol.strip().upper()
    probe = _verb_probe(
        symbol, broker=broker, account_id=account_id, proposal_id=proposal_id
    )
    adapter, refusal = _verb_gate(probe)
    if refusal is not None:
        return {"status": "rejected", "reason": refusal}
    rows = [
        r
        for r in list_open_book_entries(
            symbol, broker=broker, account_id=account_id, db_path=db_path
        )
        if r.get("id") in execution_ids
    ]
    if not rows:
        return {"status": "nothing_to_cancel", "reason": "no working book entry"}
    cancelled: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for row in rows:
        broker_order_id = str(row["broker_order_id"])
        try:
            await adapter.acancel_order(broker_order_id)
        except Exception as exc:
            logger.exception("entry cancel: broker refused %s", broker_order_id)
            failed.append({"execution_id": row["id"], "reason": str(exc)})
            continue
        cancelled.append({"execution_id": row["id"], "qty": str(row.get("qty"))})
    if cancelled and not failed:
        status, reason = "cancelled", None
    elif cancelled:
        status, reason = "partial", f"{len(failed)} refused by the broker"
    else:
        status, reason = "failed", failed[0]["reason"]
    try:
        await notify(
            kind="execution",
            title=f"Entry cancelled — {symbol}" if cancelled else f"Entry cancel failed — {symbol}",
            body=(
                f"{symbol}: cancel sent for {len(cancelled)} working book entr"
                f"{'y' if len(cancelled) == 1 else 'ies'}"
                + (f"; {len(failed)} refused by the broker" if failed else "")
                + ". Any shares filled on the way out get their stop from the guard."
            ),
            severity="info",
            account_id=account_id,
            ref_type="proposal",
            ref_id=proposal_id,
            push=False,
            db_path=db_path,
        )
    except Exception:
        logger.exception("entry cancel: failed to notify for %s", symbol)
    out: dict[str, Any] = {"status": status, "cancelled": cancelled, "failed": failed}
    if reason:
        out["reason"] = reason
    return out


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
            occ_symbol=intent.occ_symbol,
            risk_checks_json={},
            db_path=db_path,
        )

        # Stops taken down for this order, and whether the sell they were taken
        # down for actually reached the broker. Read by the `finally` below: an
        # exit that never happened must not leave the position bare.
        released: list[_ReleasedStop] = []
        release_adapter: Any = None
        sell_placed = False
        place_attempted = False

        # The release path keeps the submit lock all the way to a placed exit
        # (see the release notes above); every other order hands it back after
        # validation exactly as before.
        lock = _submit_lock()
        lock_held = False

        try:
            await lock.acquire()
            lock_held = True
            try:
                risk = await RiskEngine.validate(
                    intent, db_path=db_path, exclude_execution_id=execution_id
                )
                if not risk.passed and _release_is_warranted(intent, risk, mode):
                    # Blocked only by our own resting protective stops. Take them
                    # down inside the lock — no other submit may see the window
                    # where the position is uncommitted — then re-poll the broker
                    # and ask the RiskEngine again against what is actually there.
                    release_adapter = _adapter_for(intent)
                    await _release_protective_stops(
                        intent,
                        release_adapter,
                        released,
                        exit_execution_id=execution_id,
                        db_path=db_path,
                    )
                    if released and await _refresh_snapshot(
                        release_adapter,
                        intent,
                        db_path=db_path,
                        gone_order_ids=_gone_ids(released),
                    ):
                        risk = await RiskEngine.validate(
                            intent,
                            db_path=db_path,
                            exclude_execution_id=execution_id,
                        )
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
            finally:
                # A release leaves the position momentarily uncommitted: the stop
                # is cancelled and the exit is not placed yet, so a snapshot taken
                # in between shows the whole position sellable and a concurrent
                # sell passes on shares this one is about to take. Hold the lock
                # through the place and the re-poll below (adversarial review
                # HIGH-1, reproduced as two 50-share sells against 50 held).
                if not released and lock_held:
                    lock.release()
                    lock_held = False

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
                adapter = _adapter_for(intent)
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
                    place_attempted = True
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
                sell_placed = True
                now = datetime.now(UTC).isoformat()
                update_execution_status(
                    execution_id,
                    status,
                    broker_order_id=broker_order_id,
                    submitted_at=now,
                    db_path=db_path,
                )
                if released:
                    # Everything from here is BEST EFFORT ON TOP OF A LIVE ORDER.
                    # The exit is at the broker and the row says so; an exception
                    # escaping here would reach the outer handler, mark a live
                    # order 'error' — which reconcile never revisits, since it
                    # polls only submitted/partially_filled — and lose the fill,
                    # the re-stop and the page all at once (adversarial review
                    # HIGH-2). Failures degrade to a page instead.
                    try:
                        # Re-poll now that the exit is AT the broker. Two jobs:
                        # the snapshot that gates the next order must show the
                        # shares this one just committed, and the re-stop must be
                        # bounded by what is genuinely uncommitted rather than by
                        # our own bookkeeping (adversarial review C1/C2).
                        refreshed = await _refresh_snapshot(
                            release_adapter,
                            intent,
                            db_path=db_path,
                            gone_order_ids=_gone_ids(released),
                        )
                        budget, price = (
                            _uncommitted_shares(
                                intent,
                                exit_order_id=broker_order_id,
                                exit_qty=intent.qty,
                                db_path=db_path,
                            )
                            if refreshed
                            else (None, Decimal("0"))
                        )
                    except Exception:
                        logger.exception(
                            "stop release: could not read the position back after "
                            "placing exit %s", execution_id,
                        )
                        budget, price = None, Decimal("0")
                    finally:
                        if lock_held:
                            lock.release()
                            lock_held = False
                    try:
                        restopped = await _restop_residual(
                            released,
                            intent.qty,
                            budget=budget,
                            price=price,
                            db_path=db_path,
                        )
                        await _notify_stops_released(
                            released,
                            intent,
                            execution_id,
                            restopped=restopped,
                            db_path=db_path,
                        )
                    except Exception:
                        logger.exception(
                            "stop release: re-stop failed after exit %s", execution_id
                        )
                        for stop in released:
                            await _page_stop_gone(
                                stop,
                                qty=stop.qty,
                                reason=(
                                    "the exit was placed but the re-stop could "
                                    "not be completed"
                                ),
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
            if not sell_placed:
                # NEVER downgrade a row that already reached the broker: reconcile
                # polls submitted/partially_filled only, so an 'error' row over a
                # live order is never revisited and its fill is lost for good.
                update_execution_status(
                    execution_id,
                    "error",
                    risk_checks_json={"error": str(exc)},
                    db_path=db_path,
                )
            raise
        finally:
            if lock_held:
                lock.release()
                lock_held = False
            # Every path that took stops down without getting a sell to the
            # broker — a second rejection, a broker error, a bug — puts them
            # back. Runs outside the submit lock, which _place_stop re-takes.
            if released and not sell_placed:
                await _restore_protective_stops(
                    released,
                    adapter=release_adapter,
                    exit_execution_id=execution_id,
                    place_attempted=place_attempted,
                    db_path=db_path,
                )
