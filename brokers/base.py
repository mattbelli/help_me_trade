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


@dataclass(frozen=True)
class PlaceOrderRequest:
    """Broker order placement payload (Decimal fields as strings)."""

    client_order_id: str
    symbol: str
    side: str
    qty: str
    order_type: str
    limit_price: str | None
    time_in_force: str


@dataclass(frozen=True)
class PlaceOrderResponse:
    """Broker acknowledgement after placing an order."""

    broker_order_id: str
    status: str
    raw: dict


@runtime_checkable
class BrokerAdapter(Protocol):
    """Every broker (Alpaca, Schwab, ...) implements this interface."""

    def get_account(self) -> Account: ...

    def get_positions(self) -> list[Position]: ...

    def get_open_orders(self) -> list[Order]: ...

    async def aget_account(self) -> Account: ...

    async def aget_positions(self) -> list[Position]: ...

    async def aget_open_orders(self) -> list[Order]: ...

    async def afetch_snapshot(
        self,
    ) -> tuple[Account, list[Position], list[Order]]: ...

    async def aplace_order(self, req: PlaceOrderRequest) -> PlaceOrderResponse: ...

    async def acancel_order(self, broker_order_id: str) -> None: ...

    async def aget_order(self, broker_order_id: str) -> Order: ...
