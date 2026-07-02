"""Reconstruct a ~month daily equity curve for the real Schwab accounts.

Schwab has no account-value history API, so we ANCHOR at the live liquidation
value and walk backward through every cash flow (trades, deposits/withdrawals,
dividends/interest) from the Transactions API. On a day the account ended fully
in CASH, cash == account value, so the reconstructed point is **exact**. On a day
with an overnight position we can't value the holding (margin/multipliers), so we
**skip that day** (honest gap) rather than show a wrong figure.

Flat/held status is walked backward from the current live position (the account
is known-flat-or-not right now), so it never depends on incomplete pre-window
history. The latest point always equals the real current balance.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from imports.money import parse_dollar

_ET = ZoneInfo("America/New_York")
_CACHE_TTL = 90.0
_EPS = Decimal("0.000001")
_cache: dict = {}
_lock = threading.Lock()


def _tx_et_date(tx: dict[str, Any]) -> str | None:
    raw = tx.get("tradeDate") or tx.get("time") or tx.get("settlementDate") or ""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        if "T" in raw:
            return (
                datetime.fromisoformat(raw.replace("Z", "+00:00"))
                .astimezone(_ET)
                .date()
                .isoformat()
            )
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _tx_cash(tx: dict[str, Any]) -> Decimal:
    net = tx.get("netAmount")
    if net is not None:
        return parse_dollar(str(net))
    total = Decimal("0")
    for leg in tx.get("transferItems") or []:
        if isinstance(leg, dict) and leg.get("amount") is not None:
            total += parse_dollar(str(leg.get("amount")))
    return total


def _trade_legs(tx: dict[str, Any]):
    """(symbol, signed_qty) for each security leg of a TRADE transaction."""
    for leg in tx.get("transferItems") or []:
        if not isinstance(leg, dict):
            continue
        instr = leg.get("instrument") or {}
        if instr.get("assetType") in (None, "CURRENCY"):
            continue
        symbol = str(instr.get("uniformSymbol") or instr.get("symbol") or "")
        amount = leg.get("amount")
        if symbol and amount is not None:
            yield symbol, parse_dollar(str(amount))


def schwab_equity_curve(account_id: str | None = None, *, days: int = 30) -> dict[str, Any]:
    """Daily equity points on FLAT (all-cash) days only, for a real Schwab account
    (``schwab_<last4>``) or, when ``account_id`` is None, all real accounts."""
    key = f"{account_id}|{days}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < _CACHE_TTL:
            return hit[1]
    result = _compute(account_id, days)
    with _lock:
        _cache[key] = (now, result)
    return result


def _compute(account_id: str | None, days: int) -> dict[str, Any]:
    from brokers.schwab_accounts import get_schwab_accounts_sync
    from brokers.schwab_auth import create_client

    live = get_schwab_accounts_sync()
    if not live.get("authenticated"):
        return {"authenticated": False, "points": []}
    accounts = live.get("accounts", [])
    if account_id:
        accounts = [a for a in accounts if a.get("id") == account_id]
    if not accounts:
        return {"authenticated": True, "points": []}

    client = create_client()
    resp = client.get_account_numbers()
    resp.raise_for_status()
    last4_to_hash = {
        str(a.get("accountNumber", ""))[-4:]: a.get("hashValue", "")
        for a in resp.json()
    }

    end = datetime.now(_ET).date()
    start = end - timedelta(days=days)
    current_liq = sum(Decimal(str(a.get("liquidation_value") or 0)) for a in accounts)

    # current holdings (anchor for the backward position walk)
    current_pos: dict[tuple, Decimal] = defaultdict(lambda: Decimal("0"))
    for acct in accounts:
        for pos in acct.get("positions") or []:
            current_pos[(acct["last4"], pos["symbol"])] += Decimal(str(pos.get("qty") or 0))

    cash_delta: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    pos_delta: dict[str, dict[tuple, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    for acct in accounts:
        account_hash = last4_to_hash.get(acct.get("last4"))
        if not account_hash:
            continue
        r = client.get_transactions(
            account_hash,
            start_date=datetime.combine(start, datetime.min.time()),
            end_date=datetime.combine(end, datetime.max.time()),
        )
        r.raise_for_status()
        for tx in r.json() or []:
            day = _tx_et_date(tx)
            if not day:
                continue
            cash_delta[day] += _tx_cash(tx)
            if str(tx.get("type", "")).upper() == "TRADE":
                for symbol, qty in _trade_legs(tx):
                    pos_delta[day][(acct["last4"], symbol)] += qty

    all_days = [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]
    points, skipped = _reconstruct_flat_days(
        all_days, cash_delta, pos_delta, current_liq, current_pos
    )
    return {
        "authenticated": True,
        "current_liquidation": float(current_liq),
        "days": days,
        "skipped_held_days": skipped,
        "points": points,
    }


def _reconstruct_flat_days(
    all_days: list[str],
    cash_delta: dict[str, Decimal],
    pos_delta: dict[str, dict[tuple, Decimal]],
    current_liq: Decimal,
    current_pos: dict[tuple, Decimal],
) -> tuple[list[dict[str, Any]], int]:
    """Walk backward from today's anchor (current_liq / current_pos), emitting an
    exact equity point only on days the account ended FLAT (no holdings); skip the
    rest. Pure — unit-tested. Returns (points ascending, skipped_held_days)."""
    running_eq = current_liq
    running_pos = dict(current_pos)
    points: list[dict[str, Any]] = []
    skipped = 0
    for day in reversed(all_days):
        if all(abs(v) < _EPS for v in running_pos.values()):
            points.append({"date": day, "equity": round(float(running_eq), 2)})
        else:
            skipped += 1
        running_eq -= cash_delta.get(day, Decimal("0"))
        for key, qty in pos_delta.get(day, {}).items():
            running_pos[key] = running_pos.get(key, Decimal("0")) - qty
    points.reverse()
    return points, skipped
