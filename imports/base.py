"""Shared types for trade file imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


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


@dataclass
class ImportParseResult:
    from_date: str | None
    to_date: str | None
    events: list[ParsedTradeEvent]
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0
