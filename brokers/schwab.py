"""Charles Schwab adapter via schwab-py."""

from __future__ import annotations

import asyncio

from schwab.client import Client

from brokers.base import Account, Order, Position
from brokers.errors import BrokerError
from brokers.schwab_auth import OPEN_ORDER_STATUSES, create_client, resolve_account_hash


class SchwabError(BrokerError):
    """Schwab API request failed."""


class SchwabAdapter:
    def __init__(self, client, account_hash: str) -> None:
        self._client = client
        self._account_hash = account_hash

    @classmethod
    def from_env(cls) -> SchwabAdapter:
        return cls.from_settings()

    @classmethod
    def from_settings(cls) -> SchwabAdapter:
        try:
            client = create_client(interactive=False)
            account_hash = resolve_account_hash(client)
        except BrokerError:
            raise
        except Exception as exc:
            raise SchwabError(f"Failed to create Schwab client: {exc}") from exc
        return cls(client, account_hash)

    def _account_payload(self) -> dict:
        response = self._client.get_account(
            self._account_hash,
            fields=[Client.Account.Fields.POSITIONS],
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_account(data: dict, account_hash: str) -> Account:
        sec = data.get("securitiesAccount", data)
        balances = sec.get("currentBalances") or sec.get("initialBalances") or {}

        cash = float(
            balances.get("cashBalance")
            or balances.get("availableFunds")
            or balances.get("cashAvailableForTrading")
            or 0
        )
        buying_power = float(balances.get("buyingPower") or cash)
        portfolio_value = float(
            balances.get("liquidationValue")
            or balances.get("accountValue")
            or balances.get("totalAccountValue")
            or cash
        )

        return Account(
            id=str(sec.get("accountNumber", account_hash)),
            status="ACTIVE",
            cash=cash,
            buying_power=buying_power,
            portfolio_value=portfolio_value,
        )

    @staticmethod
    def _parse_positions(data: dict) -> list[Position]:
        sec = data.get("securitiesAccount", data)
        raw_positions = sec.get("positions") or []

        positions: list[Position] = []
        for item in raw_positions:
            long_qty = float(item.get("longQuantity") or 0)
            short_qty = float(item.get("shortQuantity") or 0)
            qty = long_qty if long_qty > 0 else short_qty
            if qty == 0:
                continue

            avg_price = float(item.get("averagePrice") or 0)
            market_value = float(item.get("marketValue") or 0)
            current_price = (
                abs(market_value / qty) if qty and market_value else avg_price
            )
            unrealized = float(item.get("longOpenProfitLoss") or 0) + float(
                item.get("shortOpenProfitLoss") or 0
            )

            symbol = item.get("symbol")
            if not symbol:
                symbol = (item.get("instrument") or {}).get("symbol", "")

            positions.append(
                Position(
                    symbol=str(symbol),
                    qty=qty,
                    side="long" if long_qty >= short_qty else "short",
                    market_value=market_value,
                    unrealized_pl=unrealized,
                    avg_entry_price=avg_price,
                    current_price=current_price,
                )
            )
        return positions

    def _fetch_open_orders(self) -> list[Order]:
        response = self._client.get_orders_for_account(self._account_hash)
        response.raise_for_status()
        raw_orders = response.json() or []
        return self._parse_orders(raw_orders)

    @staticmethod
    def _parse_orders(raw_orders: list) -> list[Order]:
        orders: list[Order] = []
        for item in raw_orders:
            status = str(item.get("status", ""))
            if status not in OPEN_ORDER_STATUSES:
                continue

            legs = item.get("orderLegCollection") or []
            symbol = ""
            side = ""
            if legs:
                leg = legs[0]
                symbol = str(
                    leg.get("instrument", {}).get("symbol", leg.get("symbol", ""))
                )
                side = str(leg.get("instruction", "")).split("_")[0].lower()

            qty = float(item.get("quantity") or 0)
            filled = float(item.get("filledQuantity") or 0)
            price = item.get("price")

            orders.append(
                Order(
                    id=str(item.get("orderId", "")),
                    symbol=symbol,
                    qty=qty,
                    side=side or "buy",
                    order_type=str(item.get("orderType", "")).lower(),
                    status=status.lower(),
                    filled_qty=filled,
                    limit_price=float(price) if price not in (None, "") else None,
                )
            )
        return orders

    def fetch_snapshot(self) -> tuple[Account, list[Position], list[Order]]:
        """One account round-trip (with positions) plus orders."""
        data = self._account_payload()
        account = self._parse_account(data, self._account_hash)
        positions = self._parse_positions(data)
        orders = self._fetch_open_orders()
        return account, positions, orders

    async def afetch_snapshot(
        self,
    ) -> tuple[Account, list[Position], list[Order]]:
        return await asyncio.to_thread(self.fetch_snapshot)

    def get_account(self) -> Account:
        return self.fetch_snapshot()[0]

    async def aget_account(self) -> Account:
        return (await self.afetch_snapshot())[0]

    def get_positions(self) -> list[Position]:
        data = self._account_payload()
        return self._parse_positions(data)

    async def aget_positions(self) -> list[Position]:
        return (await self.afetch_snapshot())[1]

    def get_open_orders(self) -> list[Order]:
        return self._fetch_open_orders()

    async def aget_open_orders(self) -> list[Order]:
        return (await self.afetch_snapshot())[2]
