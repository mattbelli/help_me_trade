"""Parser for Schwab brokerage transaction JSON exports."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from imports.base import ImportParseResult, ParsedTradeEvent, extract_broker_txn_id
from imports.money import parse_dollar, parse_mmddyyyy, parse_quantity

BUY_ACTIONS = frozenset(
    {"buy", "buy to open", "buy to close", "reinvest shares", "reinvest dividend"}
)
SELL_ACTIONS = frozenset(
    {"sell", "sell to open", "sell to close", "sell short", "short sale"}
)
OTHER_ACTIONS = frozenset(
    {
        "expired",
        "assigned",
        "qualified dividend",
        "credit interest",
        "bank interest",
        "journal",
        "wire funds",
        "electronic funds",
        "ach",
        "money market",
        "foreign tax paid",
        "dividend",
        "cash dividend",
    }
)


def _normalize_action(action: str) -> str:
    return " ".join(str(action).strip().lower().split())


def _classify_action(action: str) -> tuple[str, str | None]:
    normalized = _normalize_action(action)
    if normalized in BUY_ACTIONS or normalized.startswith("buy"):
        return "trade", "buy"
    if normalized in SELL_ACTIONS or normalized.startswith("sell"):
        return "trade", "sell"
    if normalized in OTHER_ACTIONS or "dividend" in normalized or "interest" in normalized:
        if "dividend" in normalized:
            return "dividend", None
        if "interest" in normalized:
            return "interest", None
        if normalized == "expired":
            return "option_expired", None
        return "other", None
    if normalized == "journal":
        return "transfer", None
    return "other", None


def _external_key(
    account_id: str,
    trade_date: date,
    action: str,
    symbol: str | None,
    quantity: Decimal | None,
    price: Decimal | None,
    amount: Decimal,
    fees: Decimal,
) -> str:
    parts = [
        account_id,
        trade_date.isoformat(),
        _normalize_action(action),
        (symbol or "").upper(),
        format(quantity, "f") if quantity is not None else "",
        format(price, "f") if price is not None else "",
        format(amount, "f"),
        format(fees, "f"),
    ]
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()


def _parse_row(
    row: dict[str, Any],
    *,
    account_id: str,
    index: int,
    warnings: list[str],
) -> ParsedTradeEvent | None:
    action_raw = str(row.get("Action", "")).strip()
    if not action_raw:
        warnings.append(f"Row {index}: missing Action — skipped")
        return None

    try:
        trade_date = date.fromisoformat(parse_mmddyyyy(str(row.get("Date", ""))))
    except ValueError:
        warnings.append(f"Row {index}: invalid Date — skipped")
        return None

    symbol_raw = str(row.get("Symbol", "")).strip()
    symbol = symbol_raw.upper() if symbol_raw else None

    event_type, side = _classify_action(action_raw)
    quantity = parse_quantity(row.get("Quantity"))
    price_raw = row.get("Price")
    price = parse_dollar(price_raw) if str(price_raw or "").strip() else None
    if price is not None and price == Decimal("0") and not str(price_raw or "").strip():
        price = None
    fees = parse_dollar(row.get("Fees & Comm"))
    amount = parse_dollar(row.get("Amount"))
    description = str(row.get("Description", "")).strip()

    external_key = _external_key(
        account_id,
        trade_date,
        action_raw,
        symbol,
        quantity,
        price,
        amount,
        fees,
    )

    return ParsedTradeEvent(
        event_type=event_type,
        trade_date=trade_date,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        fees=fees,
        amount=amount,
        description=description,
        external_key=external_key,
        raw=row,
        broker_txn_id=extract_broker_txn_id(row),
    )


def parse_schwab_transactions_json(
    payload: dict[str, Any],
    *,
    account_id: str = "preview",
) -> ImportParseResult:
    warnings: list[str] = []
    skipped = 0
    events: list[ParsedTradeEvent] = []

    rows = payload.get("BrokerageTransactions")
    if not isinstance(rows, list):
        raise ValueError("Missing BrokerageTransactions array")

    from_date = None
    to_date = None
    try:
        if payload.get("FromDate"):
            from_date = parse_mmddyyyy(str(payload["FromDate"]))
        if payload.get("ToDate"):
            to_date = parse_mmddyyyy(str(payload["ToDate"]))
    except ValueError:
        pass

    seen_keys: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            skipped += 1
            warnings.append(f"Row {index}: not an object — skipped")
            continue
        parsed = _parse_row(row, account_id=account_id, index=index, warnings=warnings)
        if parsed is None:
            skipped += 1
            continue
        base_key = parsed.external_key
        occurrence = seen_keys.get(base_key, 0)
        seen_keys[base_key] = occurrence + 1
        if occurrence:
            parsed = ParsedTradeEvent(
                event_type=parsed.event_type,
                trade_date=parsed.trade_date,
                symbol=parsed.symbol,
                side=parsed.side,
                quantity=parsed.quantity,
                price=parsed.price,
                fees=parsed.fees,
                amount=parsed.amount,
                description=parsed.description,
                external_key=f"{base_key}:{occurrence}",
                raw=parsed.raw,
                broker_txn_id=parsed.broker_txn_id,
            )
        events.append(parsed)

    return ImportParseResult(
        from_date=from_date,
        to_date=to_date,
        events=events,
        warnings=warnings,
        skipped=skipped,
    )


def parse_schwab_transactions_bytes(
    data: bytes,
    *,
    account_id: str = "preview",
) -> ImportParseResult:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return parse_schwab_transactions_json(payload, account_id=account_id)
