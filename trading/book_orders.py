"""Book order-management verbs — the analyst asks, the founder taps, the desk acts.

A nightly book can only *propose*. Until now that meant new entries; a stop it
wanted moved or a standing GTC entry it wanted pulled was a sentence in
``holds_review`` for the founder to act on by hand. These three verbs make
each of those a normal phone proposal — same row, same biometric tap, same
push — resolved against what is actually resting at the broker at approval:

  move_stop     — every protective stop the app rests on the symbol re-triggers
                  at the new price (trading/execution_service.move_protective_stops);
                  an entry not yet filled has its STORED stop restamped so the
                  guard arms the new price when it fills.
  cancel_entry  — every working book BUY on the symbol is cancelled
                  (execution_service.cancel_book_entries); shares filled on the
                  way out get their stop from the guard as usual.
  trim          — sell N held shares, market at the next open (or at approval
                  after the auction cutoff). This is a plain sell proposal: the
                  chokepoint's release path takes the stop down for it and
                  re-stops whatever is left.

Every verb is created only for a book source, paper-only, deny-by-default, and
never touches the broker at creation time — creation validates against the
snapshot and mints the row; the tap does the work.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import is_kill_switch_active, trading_config
from db.accounts import get_account
from db.base import DEFAULT_DB_PATH
from db.order_executions import (
    get_execution_by_proposal,
    list_open_book_entries,
    list_open_protective_stops,
)
from db.paper_orders import (
    get_proposal,
    list_executed_book_entries,
    list_pending_proposals,
    set_book_entry_stop_price,
)
from db.sqlite import get_latest_snapshot

logger = logging.getLogger(__name__)


def _book_ttl_seconds() -> int:
    """Nightly proposals live until the next regular close (spec §5.5)."""
    from trading.market_hours import next_session_close
    from trading.proposal_intake import AGENT_PROPOSAL_TTL_SECONDS

    return max(
        AGENT_PROPOSAL_TTL_SECONDS,
        int((next_session_close() - datetime.now(UTC)).total_seconds()),
    )


def _resolve_account(account_id: str | None, *, db_path: Path) -> dict[str, Any] | None:
    if not account_id:
        return None
    account = get_account(account_id, db_path=db_path)
    if (
        account is None
        or not account.get("enabled")
        or not account.get("execution_capable")
    ):
        return None
    return account


def _gate(source: str, account_id: str | None, *, db_path: Path) -> dict[str, Any]:
    if not source.startswith("book_"):
        return {"status": "invalid", "reason": "order changes are book-only"}
    if is_kill_switch_active():
        return {"status": "kill_switch"}
    cfg = trading_config()
    if not cfg.get("agent_proposals_enabled", True):
        return {"status": "disabled"}
    if cfg.get("execution_mode") != "paper":
        # The tap would only ever be refused (the verbs are paper-only) — don't
        # mint a proposal that cannot succeed.
        return {"status": "disabled", "reason": "order changes are paper-only"}
    account = _resolve_account(account_id, db_path=db_path)
    if account is None:
        return {"status": "invalid_account"}
    return {"status": "ok", "account": account}


def _pending_duplicate(
    symbol: str, order_action: str | None, account_id: str, *, db_path: Path
) -> bool:
    for p in list_pending_proposals(account_id=account_id, db_path=db_path):
        if str(p.get("symbol") or "").upper() != symbol:
            continue
        if p.get("order_action") == order_action:
            return True
    return False


def _held_position(
    broker: str, account_id: str, symbol: str, *, db_path: Path
) -> tuple[Decimal, Decimal | None] | None:
    """(qty, current_price) of the long position in the latest snapshot, or None."""
    snapshot = get_latest_snapshot(broker, account_id=account_id, db_path=db_path)
    if snapshot is None:
        return None
    for pos in snapshot.get("positions") or []:
        if str(pos.get("symbol") or "").upper() != symbol:
            continue
        if pos.get("side") != "long":
            return None
        try:
            qty = Decimal(str(pos.get("qty")))
        except Exception:
            return None
        try:
            price = Decimal(str(pos.get("current_price")))
            price = price if price > 0 else None
        except Exception:
            price = None
        return (qty, price) if qty > 0 else None
    return None


def _entry_stop_live(entry_proposal_id: str, *, db_path: Path) -> bool:
    """Same reading as the stop guard: any row under stop|<entry> that is not a
    failed attempt means a stop exists (or is being placed) for the entry."""
    from trading.stop_guard import _STOP_RETRYABLE_STATUSES

    row = get_execution_by_proposal(f"stop|{entry_proposal_id}", db_path=db_path)
    return row is not None and row.get("status") not in _STOP_RETRYABLE_STATUSES


async def _reference_price(symbol: str) -> Decimal | None:
    """Live quote when there is one, else the last completed close — the same
    basis the RiskEngine prices an off-hours stop against."""
    from trading.risk_engine import _live_market_price, _offhours_reference_price

    price = await _live_market_price(symbol)
    if price is None or price <= 0:
        price = await _offhours_reference_price(symbol)
    return price if price is not None and price > 0 else None


def _fmt_qty(qty: Decimal) -> str:
    return str(int(qty)) if qty == qty.to_integral_value() else str(qty)


def _unarmed_entries(symbol: str, account_id: str | None, *, db_path: Path) -> list[dict[str, Any]]:
    """Executed book entries with a stored stop and no live stop row — an
    unfilled GTC entry, or one whose guard placement is failing. The guard arms
    these from the STORED price, so a move restamps that instead."""
    return [
        e
        for e in list_executed_book_entries(symbol, account_id=account_id, db_path=db_path)
        if (e.get("proposal") or {}).get("stop_price") is not None
        and not _entry_stop_live(str(e["id"]), db_path=db_path)
    ]


# --- creation (nightly cycle) --------------------------------------------------


async def propose_move_stop(
    *,
    symbol: str,
    stop_price: Decimal,
    account_id: str | None,
    source: str,
    reason: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    from trading.proposals import create_order_change_proposal

    symbol = symbol.strip().upper()
    gate = _gate(source, account_id, db_path=db_path)
    if gate["status"] != "ok":
        return gate
    account = gate["account"]
    broker = str(account["broker"]).strip().lower()
    if stop_price <= 0:
        return {"status": "invalid", "reason": "stop_price must be positive"}
    if _pending_duplicate(symbol, "move_stop", account["id"], db_path=db_path):
        return {"status": "duplicate"}

    resting = list_open_protective_stops(
        symbol, broker=broker, account_id=account["id"], db_path=db_path
    )
    unarmed = _unarmed_entries(symbol, account["id"], db_path=db_path)
    if not resting and not unarmed:
        return {"status": "nothing_to_move"}
    # A trigger at or above the market fires as a market sell on accept — the
    # stop guard refuses it (and pages) when it comes to place, so refuse it
    # here for BOTH branches: a resting stop is checked against the held
    # position's price, an unfilled entry against the same reference the
    # RiskEngine would use.
    held = _held_position(broker, account["id"], symbol, db_path=db_path)
    if resting and held is None:
        return {"status": "invalid", "reason": "no held long position in the snapshot"}
    price = held[1] if held is not None else await _reference_price(symbol)
    if price is None:
        return {"status": "invalid", "reason": "no price to check the stop against"}
    if price <= stop_price:
        return {
            "status": "stop_above_market",
            "reason": f"stop {stop_price} is at/above the last price {price}",
        }
    # Pin the ENTRIES this move is for (stop|<entry> is stable across a
    # re-stop; a stop or entry that appears later is not this proposal's).
    entry_ids = sorted(
        {str(r.get("proposal_id") or "")[len("stop|"):] for r in resting if r.get("proposal_id")}
        | {str(e["id"]) for e in unarmed}
    )
    qty = sum((Decimal(str(r.get("qty") or 0)) for r in resting), Decimal("0")) or sum(
        (Decimal(str(e.get("qty") or 0)) for e in unarmed), Decimal("0")
    )
    # Execution rows carry no trigger price; the entry's stored plan does.
    old = sorted(
        {
            str((get_proposal(pid, db_path=db_path) or {}).get("proposal", {}).get("stop_price") or "?")
            for pid in entry_ids
        }
    )
    summary = (
        f"Move {symbol} stop {'/'.join(old)} → {stop_price} on {_fmt_qty(qty)} sh"
        + ("" if resting else " (arms when the entry fills)")
    )
    row = await create_order_change_proposal(
        symbol=symbol,
        order_action="move_stop",
        side="sell",
        qty=qty,
        order_type="stop",
        account_id=account["id"],
        broker=broker,
        summary=summary,
        thesis=reason,
        source=source,
        stop_price=stop_price,
        targets=entry_ids,
        ttl_seconds=_book_ttl_seconds(),
        db_path=db_path,
    )
    if "error" in row:
        return {"status": "invalid", "reason": row["error"]}
    return {"status": "proposed", "id": row["id"]}


async def propose_cancel_entry(
    *,
    symbol: str,
    account_id: str | None,
    source: str,
    reason: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    from trading.proposals import create_order_change_proposal

    symbol = symbol.strip().upper()
    gate = _gate(source, account_id, db_path=db_path)
    if gate["status"] != "ok":
        return gate
    account = gate["account"]
    broker = str(account["broker"]).strip().lower()
    if _pending_duplicate(symbol, "cancel_entry", account["id"], db_path=db_path):
        return {"status": "duplicate"}
    working = list_open_book_entries(
        symbol, broker=broker, account_id=account["id"], db_path=db_path
    )
    if not working:
        return {"status": "nothing_to_cancel"}
    qty = sum(
        (
            Decimal(str(r.get("qty") or 0)) - Decimal(str(r.get("filled_qty") or 0))
            for r in working
        ),
        Decimal("0"),
    )
    prices = sorted({str(r.get("limit_price")) for r in working if r.get("limit_price")})
    summary = f"Cancel {symbol} entry: {_fmt_qty(qty)} sh working" + (
        f" @ limit {'/'.join(prices)}" if prices else ""
    )
    row = await create_order_change_proposal(
        symbol=symbol,
        order_action="cancel_entry",
        side="buy",
        qty=qty,
        order_type=str(working[0].get("order_type") or "limit"),
        account_id=account["id"],
        broker=broker,
        summary=summary,
        thesis=reason,
        source=source,
        targets=sorted(str(r["id"]) for r in working),
        ttl_seconds=_book_ttl_seconds(),
        db_path=db_path,
    )
    if "error" in row:
        return {"status": "invalid", "reason": row["error"]}
    return {"status": "proposed", "id": row["id"]}


async def propose_trim(
    *,
    symbol: str,
    qty: Decimal,
    account_id: str | None,
    source: str,
    reason: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Sell ``qty`` held shares at the next open — a plain sell proposal with
    the book's MOO semantics; the chokepoint's release path handles the stop."""
    from trading.proposals import create_phone_proposal

    symbol = symbol.strip().upper()
    gate = _gate(source, account_id, db_path=db_path)
    if gate["status"] != "ok":
        return gate
    account = gate["account"]
    broker = str(account["broker"]).strip().lower()
    if qty <= 0 or qty != qty.to_integral_value():
        return {"status": "invalid", "reason": "trim qty must be a positive whole number"}
    if _pending_duplicate(symbol, None, account["id"], db_path=db_path):
        return {"status": "duplicate"}
    held = _held_position(broker, account["id"], symbol, db_path=db_path)
    if held is None:
        return {"status": "not_held"}
    if qty > held[0]:
        return {"status": "invalid", "reason": f"trim {qty} exceeds held {held[0]}"}
    row = await create_phone_proposal(
        {
            "symbol": symbol,
            "side": "sell",
            "qty": int(qty),
            "order_type": "market",
            "account_id": account["id"],
            "entry_style": "moo",
        },
        source=source,
        thesis=reason,
        entry_ref_price=str(held[1]) if held[1] is not None else None,
        ttl_seconds=_book_ttl_seconds(),
        db_path=db_path,
    )
    if "error" in row:
        return {"status": "invalid", "reason": row["error"]}
    return {"status": "proposed", "id": row["id"]}


# --- approval (the founder's tap) ------------------------------------------------


async def apply_order_change(
    row: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any]:
    """Run an approved order-change proposal against the broker's CURRENT state.
    Returns the chokepoint verb's outcome dict; ``status`` in
    {moved, restamped, cancelled, partial} counts as done."""
    from trading.execution_service import cancel_book_entries, move_protective_stops

    stored = row.get("proposal") or {}
    action = str(stored.get("order_action") or "")
    symbol = str(stored.get("symbol") or row.get("symbol") or "").strip().upper()
    account_id = stored.get("account_id") or row.get("account_id")
    account = _resolve_account(account_id, db_path=db_path)
    if account is None:
        return {"status": "rejected", "reason": "account not execution-capable"}
    broker = str(account["broker"]).strip().lower()
    proposal_id = str(row["id"])

    targets = frozenset(str(t) for t in (stored.get("targets") or []))

    if action == "move_stop":
        stop_price = Decimal(str(stored["stop_price"]))
        outcome = await move_protective_stops(
            symbol,
            broker=broker,
            account_id=account["id"],
            stop_price=stop_price,
            proposal_id=proposal_id,
            entry_proposal_ids=targets,
            db_path=db_path,
        )
        # Targeted entries whose stop is not resting (unfilled GTC entry, or a
        # guard retry in progress) take the new price on their stored plan so
        # the guard arms it — after the same at/above-market refusal the guard
        # itself would make. Entries whose live stop was left untouched keep
        # theirs.
        restamped = 0
        if outcome.get("status") in ("moved", "partial", "nothing_to_move"):
            unarmed = [
                e
                for e in _unarmed_entries(symbol, account["id"], db_path=db_path)
                if str(e["id"]) in targets
            ]
            if unarmed:
                held = _held_position(broker, account["id"], symbol, db_path=db_path)
                price = held[1] if held is not None else await _reference_price(symbol)
                if price is not None and price > stop_price:
                    for entry in unarmed:
                        if set_book_entry_stop_price(
                            str(entry["id"]), stop_price=str(stop_price), db_path=db_path
                        ):
                            restamped += 1
                elif outcome.get("status") == "nothing_to_move":
                    outcome["reason"] = (
                        f"stop {stop_price} is at/above the market ({price}) — the stored "
                        "plan was left as it was"
                    )
        outcome["restamped"] = restamped
        if outcome.get("status") == "nothing_to_move" and restamped:
            outcome["status"] = "restamped"
            outcome.pop("reason", None)
            try:
                from signals.notify import notify

                await notify(
                    kind="execution",
                    title=f"Stop plan updated — {symbol}",
                    body=(
                        f"{symbol}: no stop was resting; {restamped} entry plan(s) now carry a "
                        f"{stop_price} stop, armed by the guard when the entry fills."
                    ),
                    severity="info",
                    account_id=account["id"],
                    ref_type="proposal",
                    ref_id=proposal_id,
                    push=False,
                    db_path=db_path,
                )
            except Exception:
                logger.exception("stop restamp: failed to notify for %s", symbol)
        return outcome

    if action == "cancel_entry":
        return await cancel_book_entries(
            symbol,
            broker=broker,
            account_id=account["id"],
            proposal_id=proposal_id,
            execution_ids=targets,
            db_path=db_path,
        )

    return {"status": "rejected", "reason": f"unknown order_action {action!r}"}
