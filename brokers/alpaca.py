"""Alpaca paper/live adapter — reads API keys from environment."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from brokers.base import Account, Order, Position

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


from brokers.errors import BrokerError
from config.settings import alpaca_config


@dataclass(frozen=True)
class PortfolioHistoryPoint:
    timestamp: int
    equity: float
    profit_loss: float
    profit_loss_pct: float


@dataclass(frozen=True)
class StockBar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class AlpacaError(BrokerError):
    """Alpaca API request failed."""


_RANGE_TO_ALPACA: dict[str, tuple[str, str]] = {
    "1D": ("1D", "5Min"),
    "3D": ("1W", "1H"),
    "1W": ("1W", "1H"),
    "1M": ("1M", "1D"),
    "1Y": ("1A", "1D"),
    "ALL": ("all", "1D"),
}


class AlpacaAdapter:
    def __init__(
        self,
        base_url: str,
        key_id: str,
        secret: str,
        data_base_url: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v2"):
            self._base_url = self._base_url[: -len("/v2")]
        self._data_base_url = (
            (data_base_url or "https://data.alpaca.markets").strip().rstrip("/")
        )
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
        }
        self._sync_client = httpx.Client(headers=self._headers, timeout=30.0)
        self._async_client: httpx.AsyncClient | None = None

    def _async_http(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                headers=self._headers, timeout=30.0
            )
        return self._async_client

    def close(self) -> None:
        self._sync_client.close()

    async def aclose(self) -> None:
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()
        self._async_client = None

    @classmethod
    def from_env(cls) -> AlpacaAdapter:
        return cls.from_settings()

    @classmethod
    def from_settings(cls, cfg: dict[str, str] | None = None) -> AlpacaAdapter:
        merged = alpaca_config() if cfg is None else {**alpaca_config(), **cfg}
        key_id = merged.get("api_key_id", "").strip()
        secret = merged.get("api_secret_key", "").strip()
        base_url = merged.get("api_base_url", "").strip()
        data_base_url = merged.get("data_base_url", "").strip()
        missing = [
            name
            for name, val in [
                ("api_key_id", key_id),
                ("api_secret_key", secret),
                ("api_base_url", base_url),
            ]
            if not val
        ]
        if missing:
            raise AlpacaError(
                f"Missing Alpaca settings: {', '.join(missing)}. "
                "Configure in Settings or .env."
            )
        return cls(
            base_url=base_url,
            key_id=key_id,
            secret=secret,
            data_base_url=data_base_url,
        )

    def _parse_http_response(self, response: httpx.Response, path: str) -> dict | list:
        if response.status_code == 401:
            raise AlpacaError("Authentication failed (401). Check .env keys.")
        if response.status_code != 200:
            raise AlpacaError(
                f"GET {path} failed ({response.status_code}): {response.text}"
            )
        return response.json()

    def _request(
        self,
        base: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict | list:
        url = f"{base}{path}"
        try:
            response = self._sync_client.get(url, params=params)
        except httpx.RequestError as exc:
            raise AlpacaError(f"Network error: {exc}") from exc
        return self._parse_http_response(response, path)

    async def _arequest(
        self,
        base: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict | list:
        url = f"{base}{path}"
        try:
            response = await self._async_http().get(url, params=params)
        except httpx.RequestError as exc:
            raise AlpacaError(f"Network error: {exc}") from exc
        return self._parse_http_response(response, path)

    def _get(self, path: str) -> dict | list:
        return self._request(self._base_url, path)

    async def _aget(self, path: str) -> dict | list:
        return await self._arequest(self._base_url, path)

    def _get_data(self, path: str, *, params: dict[str, Any] | None = None) -> dict:
        result = self._request(self._data_base_url, path, params=params)
        if not isinstance(result, dict):
            raise AlpacaError(f"Unexpected data API response for {path}")
        return result

    async def _aget_data(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict:
        result = await self._arequest(self._data_base_url, path, params=params)
        if not isinstance(result, dict):
            raise AlpacaError(f"Unexpected data API response for {path}")
        return result

    @staticmethod
    def _parse_account(data: dict) -> Account:
        return Account(
            id=str(data["id"]),
            status=str(data.get("status", "")),
            cash=float(data.get("cash", 0)),
            buying_power=float(data.get("buying_power", 0)),
            portfolio_value=float(data.get("portfolio_value", 0)),
        )

    @staticmethod
    def _parse_positions(raw: list | dict) -> list[Position]:
        if not raw:
            return []
        if isinstance(raw, dict):
            return []

        positions: list[Position] = []
        for item in raw:
            positions.append(
                Position(
                    symbol=str(item["symbol"]),
                    qty=float(item["qty"]),
                    side=str(item.get("side", "long")),
                    market_value=float(item.get("market_value", 0)),
                    unrealized_pl=float(item.get("unrealized_pl", 0)),
                    avg_entry_price=float(item.get("avg_entry_price", 0)),
                    current_price=float(item.get("current_price", 0)),
                )
            )
        return positions

    @staticmethod
    def _parse_orders(raw: list | dict) -> list[Order]:
        if not raw:
            return []
        if isinstance(raw, dict):
            return []

        orders: list[Order] = []
        for item in raw:
            limit = item.get("limit_price")
            orders.append(
                Order(
                    id=str(item["id"]),
                    symbol=str(item["symbol"]),
                    qty=float(item["qty"]),
                    side=str(item.get("side", "")),
                    order_type=str(item.get("type", "")),
                    status=str(item.get("status", "")),
                    filled_qty=float(item.get("filled_qty", 0)),
                    limit_price=float(limit) if limit not in (None, "") else None,
                )
            )
        return orders

    def get_account(self) -> Account:
        data = self._get("/v2/account")
        if not isinstance(data, dict):
            raise AlpacaError("Unexpected account response")
        return self._parse_account(data)

    async def aget_account(self) -> Account:
        data = await self._aget("/v2/account")
        if not isinstance(data, dict):
            raise AlpacaError("Unexpected account response")
        return self._parse_account(data)

    def get_positions(self) -> list[Position]:
        return self._parse_positions(self._get("/v2/positions"))

    async def aget_positions(self) -> list[Position]:
        raw = await self._aget("/v2/positions")
        return self._parse_positions(raw if isinstance(raw, list) else [])

    def get_open_orders(self) -> list[Order]:
        return self._parse_orders(self._get("/v2/orders?status=open"))

    async def aget_open_orders(self) -> list[Order]:
        raw = await self._aget("/v2/orders?status=open")
        return self._parse_orders(raw if isinstance(raw, list) else [])

    async def afetch_snapshot(
        self,
    ) -> tuple[Account, list[Position], list[Order]]:
        account, positions, orders = await asyncio.gather(
            self.aget_account(),
            self.aget_positions(),
            self.aget_open_orders(),
        )
        return account, positions, orders

    def _parse_portfolio_history(
        self, data: dict, range_key: str
    ) -> list[PortfolioHistoryPoint]:
        timestamps = data.get("timestamp") or []
        equities = data.get("equity") or []
        profit_loss = data.get("profit_loss") or []
        profit_loss_pct = data.get("profit_loss_pct") or []

        points: list[PortfolioHistoryPoint] = []
        for i, ts in enumerate(timestamps):
            points.append(
                PortfolioHistoryPoint(
                    timestamp=int(ts),
                    equity=float(equities[i]) if i < len(equities) else 0.0,
                    profit_loss=float(profit_loss[i]) if i < len(profit_loss) else 0.0,
                    profit_loss_pct=float(profit_loss_pct[i])
                    if i < len(profit_loss_pct)
                    else 0.0,
                )
            )

        if range_key == "3D":
            cutoff = int((datetime.now(UTC) - timedelta(days=3)).timestamp())
            points = [p for p in points if p.timestamp >= cutoff]

        return points

    def get_portfolio_history(self, range_key: str) -> list[PortfolioHistoryPoint]:
        if range_key not in _RANGE_TO_ALPACA:
            raise AlpacaError(f"Unknown range {range_key!r}")

        period, timeframe = _RANGE_TO_ALPACA[range_key]
        data = self._get(
            f"/v2/account/portfolio/history?period={period}&timeframe={timeframe}"
        )
        if not isinstance(data, dict):
            raise AlpacaError("Unexpected portfolio history response")
        return self._parse_portfolio_history(data, range_key)

    async def aget_portfolio_history(
        self, range_key: str
    ) -> list[PortfolioHistoryPoint]:
        if range_key not in _RANGE_TO_ALPACA:
            raise AlpacaError(f"Unknown range {range_key!r}")

        period, timeframe = _RANGE_TO_ALPACA[range_key]
        data = await self._aget(
            f"/v2/account/portfolio/history?period={period}&timeframe={timeframe}"
        )
        if not isinstance(data, dict):
            raise AlpacaError("Unexpected portfolio history response")
        return self._parse_portfolio_history(data, range_key)

    def get_account_activities(
        self, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        result = self._request(
            self._base_url, "/v2/account/activities", params=params or {}
        )
        if isinstance(result, list):
            return result
        return []

    async def aget_account_activities(
        self, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        result = await self._arequest(
            self._base_url, "/v2/account/activities", params=params or {}
        )
        if isinstance(result, list):
            return result
        return []

    @staticmethod
    def _parse_stock_bars(raw_bars: list) -> list[StockBar]:
        bars: list[StockBar] = []
        for item in raw_bars:
            bars.append(
                StockBar(
                    time=str(item["t"]),
                    open=float(item["o"]),
                    high=float(item["h"]),
                    low=float(item["l"]),
                    close=float(item["c"]),
                    volume=float(item.get("v", 0)),
                )
            )
        return bars

    def get_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        days: int = 180,
    ) -> list[StockBar]:
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        return self.get_stock_bars_range(
            symbol, timeframe=timeframe, start=start, end=end
        )

    async def aget_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        days: int = 180,
    ) -> list[StockBar]:
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        return await self.aget_stock_bars_range(
            symbol, timeframe=timeframe, start=start, end=end
        )

    def get_stock_bars_range(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        start: datetime,
        end: datetime,
    ) -> list[StockBar]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise AlpacaError("Symbol is required")

        params = {
            "timeframe": timeframe,
            "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10000,
            "feed": "iex",
            "sort": "asc",
        }

        data = self._get_data(f"/v2/stocks/{symbol}/bars", params=params)
        return self._parse_stock_bars(data.get("bars") or [])

    async def aget_stock_bars_range(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        start: datetime,
        end: datetime,
    ) -> list[StockBar]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise AlpacaError("Symbol is required")

        params = {
            "timeframe": timeframe,
            "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10000,
            "feed": "iex",
            "sort": "asc",
        }

        data = await self._aget_data(f"/v2/stocks/{symbol}/bars", params=params)
        return self._parse_stock_bars(data.get("bars") or [])

    _assets_cache: tuple[float, list[str]] | None = None
    _ASSETS_TTL_SEC = 86_400

    def _tradable_symbols(self) -> list[str]:
        now = time.time()
        if (
            AlpacaAdapter._assets_cache is not None
            and now - AlpacaAdapter._assets_cache[0] < self._ASSETS_TTL_SEC
        ):
            return AlpacaAdapter._assets_cache[1]

        raw = self._get("/v2/assets?status=active&asset_class=us_equity")
        return self._cache_tradable_symbols(now, raw)

    async def _atradable_symbols(self) -> list[str]:
        now = time.time()
        if (
            AlpacaAdapter._assets_cache is not None
            and now - AlpacaAdapter._assets_cache[0] < self._ASSETS_TTL_SEC
        ):
            return AlpacaAdapter._assets_cache[1]

        raw = await self._aget("/v2/assets?status=active&asset_class=us_equity")
        return self._cache_tradable_symbols(now, raw)

    def _cache_tradable_symbols(self, now: float, raw: object) -> list[str]:
        if not isinstance(raw, list):
            return []
        symbols = sorted(
            str(item["symbol"])
            for item in raw
            if isinstance(item, dict)
            and item.get("tradable")
            and str(item.get("symbol", "")).isalpha()
        )
        AlpacaAdapter._assets_cache = (now, symbols)
        return symbols

    def search_symbols(self, query: str, *, limit: int = 10) -> list[str]:
        return self._filter_symbols(self._tradable_symbols(), query, limit=limit)

    async def asearch_symbols(self, query: str, *, limit: int = 10) -> list[str]:
        symbols = await self._atradable_symbols()
        return self._filter_symbols(symbols, query, limit=limit)

    @staticmethod
    def _filter_symbols(symbols: list[str], query: str, *, limit: int) -> list[str]:
        q = query.strip().upper()
        if not q:
            return []

        prefix = [s for s in symbols if s.startswith(q)]
        contains = [s for s in symbols if q in s and not s.startswith(q)]
        merged: list[str] = []
        for group in (prefix, contains):
            for sym in group:
                if sym not in merged:
                    merged.append(sym)
                if len(merged) >= limit:
                    return merged
        return merged

    @staticmethod
    def _parse_batch_bars(raw_bars: object) -> dict[str, list[StockBar]]:
        if not isinstance(raw_bars, dict):
            raise AlpacaError("Unexpected batch bars response")

        out: dict[str, list[StockBar]] = {}
        for symbol, items in raw_bars.items():
            if not isinstance(items, list):
                continue
            bars: list[StockBar] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                bars.append(
                    StockBar(
                        time=str(item["t"]),
                        open=float(item["o"]),
                        high=float(item["h"]),
                        low=float(item["l"]),
                        close=float(item["c"]),
                        volume=float(item.get("v", 0)),
                    )
                )
            out[str(symbol).upper()] = bars
        return out

    def _batch_bar_params(
        self,
        symbols: list[str],
        *,
        timeframe: str,
        days: int,
    ) -> tuple[list[str], dict[str, Any]]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            sym = raw.strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                cleaned.append(sym)

        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        params = {
            "symbols": ",".join(cleaned),
            "timeframe": timeframe,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10000,
            "feed": "iex",
        }
        return cleaned, params

    def get_stock_bars_batch(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1Day",
        days: int = 12,
    ) -> dict[str, list[StockBar]]:
        cleaned, params = self._batch_bar_params(
            symbols, timeframe=timeframe, days=days
        )
        if not cleaned:
            return {}

        data = self._get_data("/v2/stocks/bars", params=params)
        return self._parse_batch_bars(data.get("bars") or {})

    async def aget_stock_bars_batch(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1Day",
        days: int = 12,
    ) -> dict[str, list[StockBar]]:
        cleaned, params = self._batch_bar_params(
            symbols, timeframe=timeframe, days=days
        )
        if not cleaned:
            return {}

        data = await self._aget_data("/v2/stocks/bars", params=params)
        return self._parse_batch_bars(data.get("bars") or {})

    def symbol_exists(self, symbol: str) -> bool:
        sym = symbol.strip().upper()
        if not sym:
            return False
        try:
            data = self._get(f"/v2/assets/{sym}")
            return bool(isinstance(data, dict) and data.get("tradable"))
        except AlpacaError:
            return False
