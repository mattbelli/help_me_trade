"""Map Alpaca account activities to trade ledger events."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from imports.base import ParsedTradeEvent
from imports.money import parse_dollar


def _parse_activity_date(raw: str) -> date:
    text = str(raw).strip()
    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    return date.fromisoformat(text[:10])


def alpaca_activity_to_event(item: dict[str, Any], *, account_id: str) -> ParsedTradeEvent | None:
    activity_type = str(item.get("activity_type", "")).upper()
    if activity_type not in {"FILL", "PARTIAL_FILL"}:
        return None

    symbol = str(item.get("symbol", "")).strip().upper()
    if not symbol:
        return None

    side = str(item.get("side", "")).lower()
    if side not in {"buy", "sell"}:
        return None

    qty = parse_dollar(item.get("qty"))
    price = parse_dollar(item.get("price"))
    if qty <= 0:
        return None

    fees = Decimal("0")
    net = parse_dollar(item.get("net_amount"))
    if net == 0 and price:
        gross = qty * price
        net = -gross if side == "buy" else gross

    activity_id = str(item.get("id", ""))
    trade_date = _parse_activity_date(
        str(item.get("transaction_time") or item.get("date") or "")
    )
    external_key = hashlib.sha256(
        f"alpaca|{account_id}|{activity_id}".encode()
    ).hexdigest()

    return ParsedTradeEvent(
        event_type="trade",
        trade_date=trade_date,
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price if price else None,
        fees=fees,
        amount=net,
        description=f"Alpaca {activity_type} {side} {symbol}",
        external_key=external_key,
        raw=item,
    )


def parse_alpaca_activities(
    items: list[dict[str, Any]],
    *,
    account_id: str,
) -> list[ParsedTradeEvent]:
    events: list[ParsedTradeEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = alpaca_activity_to_event(item, account_id=account_id)
        if parsed:
            events.append(parsed)
    return events
