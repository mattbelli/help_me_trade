"""Shared types for trade file imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


def extract_broker_txn_id(raw: dict[str, Any]) -> str | None:
    """Stable broker transaction id when present in import/API payloads."""
    for key in ("activityId", "ActivityId", "transactionId", "id"):
        val = raw.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


@dataclass
class ParsedTradeEvent:
    event_type: str
    trade_date: date
    symbol: str | None
    side: str | None
    quantity: Decimal | None
    price: Decimal | None
    fees: Decimal
    amount: Decimal
    description: str
    external_key: str
    raw: dict[str, Any] = field(repr=False)
    broker_txn_id: str | None = None


@dataclass
class ImportParseResult:
    from_date: str | None
    to_date: str | None
    events: list[ParsedTradeEvent]
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0
