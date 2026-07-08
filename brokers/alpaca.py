"""Alpaca paper/live adapter — reads API keys from environment."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from brokers.base import Account, Order, PlaceOrderRequest, PlaceOrderResponse, Position

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

logger = logging.getLogger(__name__)

_MAX_BAR_PAGES = 50
_BATCH_BARS_PATH = "/v2/stocks/bars"
_NON_INTRADAY_TIMEFRAMES = frozenset({"1Day", "1Week", "1Month"})
_OVERNIGHT_BARS_FEED = "boats"


from brokers.alpaca_tier import data_tier, effective_alpaca_feed
from brokers.errors import BrokerError
from config.settings import alpaca_config, normalize_alpaca_data_feed


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


class AlpacaConflictError(AlpacaError):
    """Alpaca rejected the request due to a conflict (e.g. duplicate client_order_id)."""


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
        data_feed: str = "auto",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v2"):
            self._base_url = self._base_url[: -len("/v2")]
        self._data_base_url = (
            (data_base_url or "https://data.alpaca.markets").strip().rstrip("/")
        )
        self._configured_data_feed = normalize_alpaca_data_feed(data_feed)
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
        }
        self._sync_client = httpx.Client(headers=self._headers, timeout=30.0)
        self._async_client: httpx.AsyncClient | None = None

    @property
    def _data_feed(self) -> str:
        probe = self._get_data if self._configured_data_feed == "auto" else None
        return effective_alpaca_feed(
            configured_feed=self._configured_data_feed,
            get_data=probe,
        )

    @property
    def configured_data_feed(self) -> str:
        return self._configured_data_feed

    def alpaca_data_tier(self) -> str:
        return data_tier(self)

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

    @property
    def is_paper_endpoint(self) -> bool:
        """True when this adapter's trading API base URL is Alpaca paper."""
        return "paper-api" in self._base_url

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
        data_feed = normalize_alpaca_data_feed(merged.get("data_feed", "auto"))
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
            data_feed=data_feed,
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

    async def _aget(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict | list:
        return await self._arequest(self._base_url, path, params=params)

    def _parse_post_response(self, response: httpx.Response, path: str) -> dict | list:
        if response.status_code == 401:
            raise AlpacaError("Authentication failed (401). Check .env keys.")
        if response.status_code == 422:
            raise AlpacaConflictError(
                f"POST {path} conflict (422): {response.text}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise AlpacaError(
                f"POST {path} failed ({response.status_code}): {response.text}"
            )
        return response.json()

    async def _apost(self, path: str, *, body: dict[str, Any]) -> dict | list:
        url = f"{self._base_url}{path}"
        try:
            response = await self._async_http().post(url, json=body)
        except httpx.RequestError as exc:
            raise AlpacaError(f"Network error: {exc}") from exc
        return self._parse_post_response(response, path)

    async def _adelete(self, path: str) -> None:
        url = f"{self._base_url}{path}"
        try:
            response = await self._async_http().delete(url)
        except httpx.RequestError as exc:
            raise AlpacaError(f"Network error: {exc}") from exc
        if response.status_code == 401:
            raise AlpacaError("Authentication failed (401). Check .env keys.")
        if response.status_code < 200 or response.status_code >= 300:
            raise AlpacaError(
                f"DELETE {path} failed ({response.status_code}): {response.text}"
            )

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
    def _parse_order_item(item: dict) -> Order:
        limit = item.get("limit_price")
        filled_avg = item.get("filled_avg_price")
        return Order(
            id=str(item["id"]),
            symbol=str(item["symbol"]),
            qty=float(item["qty"]),
            side=str(item.get("side", "")),
            order_type=str(item.get("type", "")),
            status=str(item.get("status", "")),
            filled_qty=float(item.get("filled_qty", 0)),
            limit_price=float(limit) if limit not in (None, "") else None,
            filled_avg_price=float(filled_avg) if filled_avg not in (None, "") else None,
        )

    @staticmethod
    def _parse_orders(raw: list | dict) -> list[Order]:
        if not raw:
            return []
        if isinstance(raw, dict):
            return []

        orders: list[Order] = []
        for item in raw:
            orders.append(AlpacaAdapter._parse_order_item(item))
        return orders

    @staticmethod
    def _parse_place_order_response(raw: dict) -> PlaceOrderResponse:
        return PlaceOrderResponse(
            broker_order_id=str(raw["id"]),
            status=str(raw.get("status", "")),
            raw=raw,
        )

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

    async def aplace_order(self, req: PlaceOrderRequest) -> PlaceOrderResponse:
        body: dict[str, Any] = {
            "symbol": req.symbol,
            "qty": req.qty,
            "side": req.side,
            "type": req.order_type,
            "time_in_force": req.time_in_force,
            "client_order_id": req.client_order_id,
        }
        if req.limit_price is not None:
            body["limit_price"] = req.limit_price
        if req.position_intent is not None:
            body["position_intent"] = req.position_intent

        try:
            raw = await self._apost("/v2/orders", body=body)
        except AlpacaConflictError as exc:
            order = await self.aget_order_by_client_id(req.client_order_id)
            if order is None:
                raise AlpacaError(
                    f"Order conflict for client_order_id {req.client_order_id!r} "
                    "but existing order not found at broker"
                ) from exc
            return PlaceOrderResponse(
                broker_order_id=order.id,
                status=order.status,
                raw={"id": order.id, "status": order.status, "recovered": True},
            )
        except AlpacaError as exc:
            # Only retry without position_intent when Alpaca specifically rejects
            # that field — never swallow unrelated 400s (bad qty, unknown symbol).
            if req.position_intent is not None and "position_intent" in str(exc).lower():
                body.pop("position_intent", None)
                raw = await self._apost("/v2/orders", body=body)
            else:
                raise

        if not isinstance(raw, dict):
            raise AlpacaError("Unexpected order placement response")
        return self._parse_place_order_response(raw)

    async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
        raw = await self._aget(
            "/v2/orders", params={"client_order_ids": client_order_id}
        )
        orders = self._parse_orders(raw if isinstance(raw, list) else [])
        return orders[0] if orders else None

    async def aget_order(self, broker_order_id: str) -> Order:
        raw = await self._aget(f"/v2/orders/{broker_order_id}")
        if not isinstance(raw, dict):
            raise AlpacaError(f"Unexpected order response for {broker_order_id}")
        return self._parse_order_item(raw)

    async def acancel_order(self, broker_order_id: str) -> None:
        await self._adelete(f"/v2/orders/{broker_order_id}")

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

    @staticmethod
    def _bar_fetch_context(path: str, params: dict[str, Any]) -> str:
        timeframe = params.get("timeframe", "?")
        if path == _BATCH_BARS_PATH:
            return f"symbols={params.get('symbols', '?')} timeframe={timeframe}"
        parts = path.rstrip("/").split("/")
        symbol = parts[-2] if len(parts) >= 2 else "?"
        return f"symbol={symbol} timeframe={timeframe}"

    @staticmethod
    def _merge_bar_pages(
        accumulated: list | dict[str, list],
        page_bars: list | dict[str, list],
    ) -> list | dict[str, list]:
        if isinstance(page_bars, list):
            if isinstance(accumulated, list):
                accumulated.extend(page_bars)
            return accumulated
        if isinstance(page_bars, dict):
            if not isinstance(accumulated, dict):
                accumulated = {}
            for symbol, items in page_bars.items():
                if isinstance(items, list):
                    accumulated.setdefault(str(symbol), []).extend(items)
        return accumulated

    def _get_all_bar_pages(
        self, path: str, params: dict[str, Any]
    ) -> dict[str, list | dict[str, list]]:
        is_batch = path == _BATCH_BARS_PATH
        accumulated: list | dict[str, list] = {} if is_batch else []
        page_token: str | None = None
        context = self._bar_fetch_context(path, params)

        for page_num in range(_MAX_BAR_PAGES):
            request_params = dict(params)
            if page_token:
                request_params["page_token"] = page_token

            data = self._get_data(path, params=request_params)
            page_bars = data.get("bars") or ({} if is_batch else [])
            accumulated = self._merge_bar_pages(accumulated, page_bars)

            page_token = data.get("next_page_token")
            if not page_token:
                break
        else:
            logger.warning(
                "Bar pagination capped at %d pages for %s",
                _MAX_BAR_PAGES,
                context,
            )

        return {"bars": accumulated}

    async def _aget_all_bar_pages(
        self, path: str, params: dict[str, Any]
    ) -> dict[str, list | dict[str, list]]:
        is_batch = path == _BATCH_BARS_PATH
        accumulated: list | dict[str, list] = {} if is_batch else []
        page_token: str | None = None
        context = self._bar_fetch_context(path, params)

        for page_num in range(_MAX_BAR_PAGES):
            request_params = dict(params)
            if page_token:
                request_params["page_token"] = page_token

            data = await self._aget_data(path, params=request_params)
            page_bars = data.get("bars") or ({} if is_batch else [])
            accumulated = self._merge_bar_pages(accumulated, page_bars)

            page_token = data.get("next_page_token")
            if not page_token:
                break
        else:
            logger.warning(
                "Bar pagination capped at %d pages for %s",
                _MAX_BAR_PAGES,
                context,
            )

        return {"bars": accumulated}

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

    @staticmethod
    def _is_intraday_timeframe(timeframe: str) -> bool:
        return timeframe not in _NON_INTRADAY_TIMEFRAMES

    @staticmethod
    def _merge_bars_by_time(*series: list[StockBar]) -> list[StockBar]:
        """Union-merge bar series by timestamp (SIP first, boats fills overnight gaps)."""
        by_time: dict[str, StockBar] = {}
        for bars in series:
            for bar in bars:
                by_time.setdefault(bar.time, bar)
        return sorted(by_time.values(), key=lambda b: b.time)

    async def _aget_bars_with_feed(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        feed: str,
    ) -> list[StockBar]:
        symbol = symbol.strip().upper()
        if not symbol:
            return []

        params = {
            "timeframe": timeframe,
            "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10000,
            "feed": feed,
            "sort": "asc",
        }
        try:
            data = await self._aget_all_bar_pages(
                f"/v2/stocks/{symbol}/bars", params
            )
            return self._parse_stock_bars(data.get("bars") or [])
        except Exception:
            logger.warning(
                "Alpaca bars fetch failed for %s (feed=%s)",
                symbol,
                feed,
                exc_info=True,
            )
            return []

    async def aget_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        days: int = 180,
        extended_hours: bool = False,
    ) -> list[StockBar]:
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        return await self.aget_stock_bars_range(
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            extended_hours=extended_hours,
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
            "feed": self._data_feed,
            "sort": "asc",
        }

        data = self._get_all_bar_pages(f"/v2/stocks/{symbol}/bars", params)
        return self._parse_stock_bars(data.get("bars") or [])

    async def aget_stock_bars_range(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        start: datetime,
        end: datetime,
        extended_hours: bool = False,
    ) -> list[StockBar]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise AlpacaError("Symbol is required")

        bars = await self._aget_bars_with_feed(
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            feed=self._data_feed,
        )
        if extended_hours and self._is_intraday_timeframe(timeframe):
            boats = await self._aget_bars_with_feed(
                symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                feed=_OVERNIGHT_BARS_FEED,
            )
            if boats:
                bars = self._merge_bars_by_time(bars, boats)
        return bars

    _assets_cache: tuple[float, list[str]] | None = None
    _overnight_cache: tuple[float, set[str]] | None = None
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
        symbols: list[str] = []
        overnight: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol", ""))
            if not sym.isalpha():
                continue
            if item.get("tradable"):
                symbols.append(sym)
            attrs = item.get("attributes") or []
            if (
                isinstance(attrs, list)
                and "overnight_tradable" in attrs
                and "overnight_halted" not in attrs
            ):
                overnight.add(sym)
        symbols.sort()
        AlpacaAdapter._assets_cache = (now, symbols)
        AlpacaAdapter._overnight_cache = (now, overnight)
        return symbols

    def overnight_tradable_symbols(self) -> set[str]:
        now = time.time()
        if (
            AlpacaAdapter._overnight_cache is not None
            and now - AlpacaAdapter._overnight_cache[0] < self._ASSETS_TTL_SEC
        ):
            return AlpacaAdapter._overnight_cache[1]

        self._tradable_symbols()
        if AlpacaAdapter._overnight_cache is not None:
            return AlpacaAdapter._overnight_cache[1]
        return set()

    async def aovernight_tradable_symbols(self) -> set[str]:
        now = time.time()
        if (
            AlpacaAdapter._overnight_cache is not None
            and now - AlpacaAdapter._overnight_cache[0] < self._ASSETS_TTL_SEC
        ):
            return AlpacaAdapter._overnight_cache[1]

        await self._atradable_symbols()
        if AlpacaAdapter._overnight_cache is not None:
            return AlpacaAdapter._overnight_cache[1]
        return set()

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
            "feed": self._data_feed,
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

        data = self._get_all_bar_pages(_BATCH_BARS_PATH, params)
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

        data = await self._aget_all_bar_pages(_BATCH_BARS_PATH, params)
        return self._parse_batch_bars(data.get("bars") or {})

    _EXT_QUOTES_CHUNK = 200

    @staticmethod
    def _parse_ext_quote_snapshot(snap: dict) -> dict | None:
        """Map one Alpaca stock snapshot to {ext_last, ext_change_pct}."""
        if not isinstance(snap, dict):
            return None

        trade = snap.get("latestTrade") or {}
        quote = snap.get("latestQuote") or {}
        daily_bar = snap.get("dailyBar") or {}
        prev_daily = snap.get("prevDailyBar") or {}

        def _f(v: object) -> float | None:
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        ext_last = _f(trade.get("p"))
        if ext_last is None:
            bid = _f(quote.get("bp"))
            ask = _f(quote.get("ap"))
            if bid is not None and ask is not None:
                ext_last = (bid + ask) / 2.0
            elif bid is not None:
                ext_last = bid
            elif ask is not None:
                ext_last = ask
        if ext_last is None:
            return None

        prev_close = _f(prev_daily.get("c"))
        reg_close = _f(daily_bar.get("c"))
        daily_t = daily_bar.get("t") or ""
        prev_t = prev_daily.get("t") or ""

        ext_change_pct: float | None = None
        ref: float | None = None
        # New trading day with its own daily bar → move vs today's RTH close
        # (post/overnight, matching Schwab's postMarketPercentChange intent).
        if reg_close and prev_close and daily_t and prev_t and daily_t > prev_t:
            ref = reg_close
        elif prev_close:
            ref = prev_close
        elif reg_close:
            ref = reg_close
        if ref and ref != 0:
            ext_change_pct = round((ext_last - ref) / ref * 100, 2)

        return {"ext_last": ext_last, "ext_change_pct": ext_change_pct}

    async def aget_ext_quotes(
        self, symbols: list[str], *, feed: str | None = None
    ) -> dict[str, dict]:
        """Snapshot batch for extended-hours last price + % move.

        Default feed is the daytime SIP/IEX feed; callers may override (e.g.
        ``boats`` / ``overnight`` during the 8pm–4am session).

        Returns {SYMBOL: {ext_last, ext_change_pct}}. Fail-soft: partial/empty on
        error, never raises — callers must not crash when Alpaca hiccups.
        """
        resolved_feed = feed if feed is not None else self._data_feed
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            sym = raw.strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                cleaned.append(sym)
        if not cleaned:
            return {}

        out: dict[str, dict] = {}
        for start in range(0, len(cleaned), self._EXT_QUOTES_CHUNK):
            chunk = cleaned[start : start + self._EXT_QUOTES_CHUNK]
            try:
                data = await self._aget_data(
                    "/v2/stocks/snapshots",
                    params={"symbols": ",".join(chunk), "feed": resolved_feed},
                )
            except Exception:
                logger.warning(
                    "Alpaca ext-quote snapshot failed for %d symbols",
                    len(chunk),
                    exc_info=True,
                )
                continue
            for sym in chunk:
                parsed = self._parse_ext_quote_snapshot(data.get(sym) or {})
                if parsed is not None:
                    out[sym] = parsed
        return out

    def get_forward_splits(
        self, symbols: list[str]
    ) -> dict[str, list[tuple[date, Decimal]]]:
        """Forward stock splits from Alpaca corporate-actions API (graceful on failure)."""
        cleaned = sorted({s.strip().upper() for s in symbols if s and s.strip()})
        if not cleaned:
            return {}

        try:
            data = self._get_data(
                "/v1/corporate-actions",
                params={
                    "symbols": ",".join(cleaned),
                    "types": "forward_split",
                    "limit": 1000,
                },
            )
        except AlpacaError:
            return {}

        raw_actions = data.get("corporate_actions") if isinstance(data, dict) else None
        if not isinstance(raw_actions, dict):
            raw_actions = data if isinstance(data, dict) else {}
        entries = raw_actions.get("forward_splits") or []
        if not isinstance(entries, list):
            return {}

        by_symbol: dict[str, list[tuple[date, Decimal]]] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol") or "").upper()
            if not sym:
                continue
            new_rate = item.get("new_rate")
            old_rate = item.get("old_rate")
            if new_rate is None or old_rate is None:
                continue
            try:
                old = Decimal(str(old_rate))
                if old <= 0:
                    continue
                ratio = Decimal(str(new_rate)) / old
            except Exception:
                continue
            eff_raw = item.get("ex_date") or item.get("process_date")
            if not eff_raw:
                continue
            try:
                eff = date.fromisoformat(str(eff_raw))
            except ValueError:
                continue
            by_symbol.setdefault(sym, []).append((eff, ratio))

        for sym, splits in by_symbol.items():
            splits.sort(key=lambda t: t[0])
        return by_symbol

    def symbol_exists(self, symbol: str) -> bool:
        sym = symbol.strip().upper()
        if not sym:
            return False
        try:
            data = self._get(f"/v2/assets/{sym}")
            return bool(isinstance(data, dict) and data.get("tradable"))
        except AlpacaError:
            return False

    def option_contract_exists(self, occ_symbol: str) -> bool:
        sym = occ_symbol.strip().upper()
        if not sym:
            return False
        try:
            data = self._get(f"/v1beta1/options/contracts/{sym}")
            return bool(isinstance(data, dict) and data.get("tradable"))
        except AlpacaError:
            return False

    @staticmethod
    def _parse_option_snapshot(data: dict, occ_symbol: str) -> dict | None:
        snapshots = data.get("snapshots") or {}
        snap = snapshots.get(occ_symbol)
        if not isinstance(snap, dict):
            return None

        quote = (
            snap.get("latestQuote")
            or snap.get("latest_quote")
            or snap.get("quote")
            or {}
        )
        if not isinstance(quote, dict):
            quote = {}

        trade = (
            snap.get("latestTrade")
            or snap.get("latest_trade")
            or snap.get("trade")
            or {}
        )
        if not isinstance(trade, dict):
            trade = {}

        bid = quote.get("bp")
        ask = quote.get("ap")
        last = trade.get("p") or trade.get("price")

        if bid is None and ask is None and last is None:
            return None

        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
        last_f = float(last) if last is not None else None
        mid: float | None = None
        if bid_f is not None and ask_f is not None:
            mid = (bid_f + ask_f) / 2.0

        as_of = quote.get("t") or trade.get("t")

        return {
            "symbol": occ_symbol,
            "bid": bid_f,
            "ask": ask_f,
            "mid": mid,
            "last": last_f,
            "as_of": as_of,
        }

    def get_option_quote(self, occ_symbol: str) -> dict | None:
        sym = occ_symbol.strip().upper()
        if not sym:
            return None
        try:
            data = self._get_data(
                f"/v1beta1/options/snapshots/{sym}",
            )
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        parsed = self._parse_option_snapshot(data, sym)
        if parsed is not None:
            return parsed
        try:
            data = self._get_data(
                "/v1beta1/options/snapshots",
                params={"symbols": sym},
            )
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return self._parse_option_snapshot(data, sym)

    async def aget_option_quote(self, occ_symbol: str) -> dict | None:
        sym = occ_symbol.strip().upper()
        if not sym:
            return None
        try:
            data = await self._aget_data(
                f"/v1beta1/options/snapshots/{sym}",
            )
        except Exception:
            data = None
        if isinstance(data, dict):
            parsed = self._parse_option_snapshot(data, sym)
            if parsed is not None:
                return parsed
        try:
            data = await self._aget_data(
                "/v1beta1/options/snapshots",
                params={"symbols": sym},
            )
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return self._parse_option_snapshot(data, sym)
