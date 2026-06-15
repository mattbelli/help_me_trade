"""Normalized broker types and adapter interface (Schwab plugs in later)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Position:
    """Broker-agnostic open position."""

    symbol: str
    qty: float
    side: str  # "long" or "short"
    market_value: float
    unrealized_pl: float
    avg_entry_price: float
    current_price: float


@dataclass(frozen=True)
class Account:
    """Broker-agnostic account summary."""

    id: str
    status: str
    cash: float
    buying_power: float
    portfolio_value: float


@dataclass(frozen=True)
class Order:
    """Broker-agnostic order (open/pending or recently submitted)."""

    id: str
    symbol: str
    qty: float
    side: str  # "buy" or "sell"
    order_type: str  # "market", "limit", etc.
    status: str  # "new", "accepted", "filled", ...
    filled_qty: float
    limit_price: float | None


@runtime_checkable
class BrokerAdapter(Protocol):
    """Every broker (Alpaca, Schwab, ...) implements this interface."""

    def get_account(self) -> Account: ...

    def get_positions(self) -> list[Position]: ...

    def get_open_orders(self) -> list[Order]: ...
