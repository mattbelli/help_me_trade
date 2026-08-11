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
_BARS_PAGE_LIMIT = 10000  # the `limit` sent on every bars request
# Alpaca does NOT always fill a page to `limit` — measured 2026-08-11, a 40-symbol
# 5Min request with limit=10000 handed back a token after 2,387 bars. So the real
# budget is under the nominal one by an unknown amount, and a chunk sized to 98%
# of nominal (which 503×1825d was: 50 of 50 pages, 491,370 bars) is one short page
# away from raising and discarding the whole chunk. Size to 70%.
_PAGE_BUDGET_SAFETY = 0.70
# Bars per SYMBOL per CALENDAR day of the requested window (callers ask in
# calendar days), ~252 sessions / 365 days.
# ⚠️ Intraday counts are EXTENDED HOURS, not RTH. Alpaca returns 04:00-20:00 ET,
# so a 5Min day is ~192 bars and not the 78 an RTH session implies — measured on
# AAPL 2026-08-10: 191 actual vs 78 assumed. Using RTH numbers here under-counted
# the budget by up to 2.5x on exactly the liquid names a batch asks about.
_SESSIONS_PER_CALENDAR_DAY: dict[str, float] = {
    "1Day": 252 / 365,
    "1Week": 52 / 365,
    "1Month": 12 / 365,
    "1Hour": (252 / 365) * 16,
    "1H": (252 / 365) * 16,
    "30Min": (252 / 365) * 32,
    "15Min": (252 / 365) * 64,
    "5Min": (252 / 365) * 192,
    "1Min": (252 / 365) * 960,
}
# A timeframe we do not know is assumed to be the DENSEST one we do. Defaulting
# to "1 bar per day" (the old fallback) resolved 15Min — a real Alpaca timeframe
# used elsewhere in this repo — to a 500-symbol chunk needing ~8M bars against a
# 500k budget. A dict miss must clamp LOW; over-chunking is the failure mode.
_UNKNOWN_TIMEFRAME_BARS = max(_SESSIONS_PER_CALENDAR_DAY.values())


def _raise_if_batch_capped(is_batch: bool, context: str) -> None:
    """A capped BATCH is silently partial in the worst possible way: Alpaca fills
    pages symbol-ALPHABETICALLY, so the tail of the alphabet vanishes and the
    caller reports it as "no data for N symbols" — indistinguishable from those
    tickers not existing. Measured 2026-08-11: a 503-symbol × 5-year request needs
    ~627k bars against a 50 × 10,000 budget, and SHW/SJM/SLB/SMCI/SNA came back
    empty while AAPL got all 1,253 sessions.

    ⚠️ Raising is only a NET improvement because `_batch_chunk_size` makes capping
    rare: a capped chunk now returns NOTHING rather than its alphabetical head, so
    the loss is bigger when it happens. Both halves are required; neither alone is
    a fix. Single-symbol paths keep warning — they lose the far end of one series,
    which is noisy rather than wrong about which symbols exist.
    """
    if is_batch:
        raise BrokerError(
            f"Bar pagination capped at {_MAX_BAR_PAGES} pages for {context}"
            " — batch result would be silently truncated; request fewer"
            " symbols per chunk or a shorter window"
        )


def _trade_at_or_after(trade: dict, cutoff: datetime) -> bool:
    """True if `trade`'s timestamp is at/after `cutoff`. Untimed trades fail."""
    raw = trade.get("t")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts >= cutoff


_NON_INTRADAY_TIMEFRAMES = frozenset({"1Day", "1Week", "1Month"})
_OVERNIGHT_BARS_FEED = "boats"


from brokers.alpaca_tier import data_tier, effective_alpaca_feed
from brokers.errors import BrokerError, NotFoundError
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


# Alpaca's order list defaults to 50 rows and silently truncates (measured
# 2026-08-02: `limit` is honoured, so the default is real). 500 is its maximum.
_OPEN_ORDERS_LIMIT = 500
_OPEN_ORDERS_PATH = f"/v2/orders?status=open&limit={_OPEN_ORDERS_LIMIT}"


class AlpacaNotFoundError(AlpacaError, NotFoundError):
    """Alpaca answered 404 — the resource does not exist for this account.

    Separated from the generic error because absence is EVIDENCE and a timeout is
    not: only a 404 lets a caller conclude an order never reached the broker (or
    was polled against the wrong account). Everything else has to be retried.
    """


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
        if response.status_code == 404:
            raise AlpacaNotFoundError(
                f"GET {path} failed (404): {response.text}"
            )
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
        stop = item.get("stop_price")
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
            time_in_force=str(item["time_in_force"]) if item.get("time_in_force") else None,
            stop_price=float(stop) if stop not in (None, "") else None,
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
        return self._parse_orders(self._get(_OPEN_ORDERS_PATH))

    async def aget_open_orders(self) -> list[Order]:
        raw = await self._aget(_OPEN_ORDERS_PATH)
        orders = self._parse_orders(raw if isinstance(raw, list) else [])
        if len(orders) >= _OPEN_ORDERS_LIMIT:
            # Truncated. This list is what RiskEngine derives pending_sell from,
            # so a dropped resting sell reads as shares that are free to sell
            # again. Never let that pass silently (measured: `limit` IS honoured
            # and defaults to 50 — the path used to send none at all).
            logger.error(
                "Open-order read hit the %d cap — pending sells may be missing",
                _OPEN_ORDERS_LIMIT,
            )
        return orders

    async def afetch_snapshot(
        self,
    ) -> tuple[Account, list[Position], list[Order]]:
        """Account, positions and open orders as three CONCURRENT reads.

        Deliberately still concurrent. Reading positions last was tried and
        reverted 2026-08-02: it does not buy an ordering invariant (measured, it
        shifts a ~21 ms probability window — Alpaca's fill-to-position lag is
        unmeasured and may exceed it), and `_uncommitted_shares` carries a
        compensation that assumes positions may be the OLDER read. Making
        positions the fresher one turns that compensation into a double-count,
        which zeroes the re-stop budget and leaves a position unprotected with
        no page (adversarial review B HIGH-1). Both halves have to move together.
        """
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
        if req.stop_price is not None:
            body["stop_price"] = req.stop_price
        if req.extended_hours:
            body["extended_hours"] = True
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
        """The order we placed under this client id, or None if the broker has none.

        Must be the singular route. `GET /v2/orders` **ignores** a
        `client_order_ids` param — measured against the live paper API on
        2026-08-02, a garbage value returned the same four open orders as no
        filter — so filtering there returns an arbitrary open order for an id the
        broker never saw, and misses any order that is already closed. Callers
        treat this answer as evidence about a specific order (bind the row to its
        broker id, decide an exit is resting, recover a 409), so a wrong order or
        a false absence is worse than an exception.
        """
        try:
            raw = await self._aget(
                "/v2/orders:by_client_order_id",
                params={"client_order_id": client_order_id},
            )
        except AlpacaNotFoundError as exc:
            # Absence only when the broker names the id we asked about. Alpaca
            # answers a missing ROUTE with the same 404 and the same numeric
            # code ("endpoint not found") — measured — and reading that as "the
            # broker has never seen this order" would restore a protective stop
            # on top of a live sell. Anything else propagates.
            if client_order_id not in str(exc):
                raise
            return None
        if not isinstance(raw, dict):
            raise AlpacaError(
                f"Unexpected order response for client_order_id {client_order_id!r}"
            )
        return self._parse_order_item(raw)

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
            _raise_if_batch_capped(is_batch, context)
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
            _raise_if_batch_capped(is_batch, context)
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
            # Alpaca defaults to adjustment=raw — history spanning a split would
            # show the pre-split price scale (CRWD 4:1 2026-07-02: $772 -> $191
            # cliff). Split-only, not "all": dividends must not shift prices.
            "adjustment": "split",
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
            "adjustment": "split",
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
            # "/" = futures notation (e.g. /CL): Alpaca stocks endpoints 400
            # the entire batch on one such symbol.
            if sym and sym not in seen and not sym.startswith("/"):
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
            "adjustment": "split",
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

        merged: dict[str, list] = {}
        size = self._batch_chunk_size(timeframe=timeframe, days=days)
        for start in range(0, len(cleaned), size):
            chunk = cleaned[start : start + size]
            # Fail-soft per chunk (same contract as aget_day_volumes): one slow
            # chunk must not discard the rest of the sweep — absent symbols
            # simply retry on the caller's next pass.
            try:
                data = self._get_all_bar_pages(
                    _BATCH_BARS_PATH, dict(params, symbols=",".join(chunk))
                )
            except Exception:
                logger.warning(
                    "Alpaca batch bars chunk failed (%d symbols, offset %d)",
                    len(chunk),
                    start,
                    exc_info=True,
                )
                continue
            merged.update(data.get("bars") or {})
        return self._parse_batch_bars(merged)

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

        merged: dict[str, list] = {}
        size = self._batch_chunk_size(timeframe=timeframe, days=days)
        for start in range(0, len(cleaned), size):
            chunk = cleaned[start : start + size]
            # Fail-soft per chunk (same contract as aget_day_volumes): one slow
            # chunk must not discard the rest of the sweep — absent symbols
            # simply retry on the caller's next pass.
            try:
                data = await self._aget_all_bar_pages(
                    _BATCH_BARS_PATH, dict(params, symbols=",".join(chunk))
                )
            except Exception:
                logger.warning(
                    "Alpaca batch bars chunk failed (%d symbols, offset %d)",
                    len(chunk),
                    start,
                    exc_info=True,
                )
                continue
            merged.update(data.get("bars") or {})
        return self._parse_batch_bars(merged)

    # httpx caps any one URL component at 65,536 chars; the fusion universe
    # (~9.8K symbols, commas URL-encoded to %2C) measures ~77KB in one query.
    _BATCH_BARS_CHUNK = 500

    @classmethod
    def _batch_chunk_size(cls, *, timeframe: str, days: int) -> int:
        """Symbols per request, capped so pagination cannot silently truncate.

        Two independent ceilings. The URL one (`_BATCH_BARS_CHUNK`) is fixed. The
        PAGE one moves with how much history is asked for: the budget is
        `_MAX_BAR_PAGES × limit` bars TOTAL across the batch, so the deeper the
        window the fewer symbols fit. A 503-symbol × 5-year daily request needs
        ~627k bars against a 500k budget — measured 2026-08-11, and it dropped
        the alphabetical tail without erroring.

        ⚠️ Returns 1 when even ONE symbol's own history exceeds the budget (e.g.
        1Min over ~5 years). That request will still cap and now raises rather
        than truncating — the floor cannot conjure headroom that does not exist,
        so the caller has to ask for a shorter window. `_batch_chunk_size` sizes
        requests; it does not make impossible ones possible.
        """
        per_symbol = days * _SESSIONS_PER_CALENDAR_DAY.get(
            timeframe, _UNKNOWN_TIMEFRAME_BARS
        )
        if per_symbol <= 0:
            return cls._BATCH_BARS_CHUNK
        budget = int(_MAX_BAR_PAGES * _BARS_PAGE_LIMIT * _PAGE_BUDGET_SAFETY / per_symbol)
        return max(1, min(cls._BATCH_BARS_CHUNK, budget))
    _EXT_QUOTES_CHUNK = 200

    @staticmethod
    def _parse_ext_quote_snapshot(
        snap: dict, *, not_before: datetime | None = None
    ) -> dict | None:
        """Map one Alpaca stock snapshot to {ext_last, ext_change_pct}.

        `not_before` rejects a snapshot whose last trade predates that instant.
        The overnight venue has no trades in most names, and its snapshot keeps
        serving the last trade it EVER saw: NTSK showed a 100-share print from
        the prior overnight session — 20.6h and one +11% regular day stale — as
        the live price, painting -10% on a stock that had closed +11%. A trade
        older than the last regular close has been superseded by that close, so
        the snapshot is treated as absent and the caller falls back to the
        consolidated feed.
        """
        if not isinstance(snap, dict):
            return None

        trade = snap.get("latestTrade") or {}
        quote = snap.get("latestQuote") or {}
        daily_bar = snap.get("dailyBar") or {}
        prev_daily = snap.get("prevDailyBar") or {}

        if not_before is not None and not _trade_at_or_after(trade, not_before):
            return None

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
        self,
        symbols: list[str],
        *,
        feed: str | None = None,
        not_before: datetime | None = None,
    ) -> dict[str, dict]:
        """Snapshot batch for extended-hours last price + % move.

        Default feed is the daytime SIP/IEX feed; callers may override (e.g.
        ``boats`` / ``overnight`` during the 8pm–4am session). `not_before` drops
        symbols whose last trade predates that instant, so a venue with no trades
        cannot serve a stale one as the live price.

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
                parsed = self._parse_ext_quote_snapshot(
                    data.get(sym) or {}, not_before=not_before
                )
                if parsed is not None:
                    out[sym] = parsed
        return out

    async def aget_day_volumes(self, symbols: list[str]) -> dict[str, tuple[int, str]]:
        """Batch today's cumulative share volume: ``{SYMBOL: (volume, ts)}``.

        ``dailyBar.v`` from ``GET /v2/stocks/snapshots``, reusing the same
        200-symbol chunking as :meth:`aget_ext_quotes`. The volume INCLUDES
        extended hours (measured 07-28) — callers wanting an RTH-only total
        subtract their own premarket baseline. ``ts`` is the daily bar's stamp
        when present, else the snapshot's own. Fail-soft: partial/empty on
        error, never raises.
        """
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            sym = raw.strip().upper()
            # "/" = futures notation (e.g. /CL): one such symbol 400s the
            # whole 200-symbol snapshot chunk.
            if sym and sym not in seen and not sym.startswith("/"):
                seen.add(sym)
                cleaned.append(sym)
        if not cleaned:
            return {}

        out: dict[str, tuple[int, str]] = {}
        for start in range(0, len(cleaned), self._EXT_QUOTES_CHUNK):
            chunk = cleaned[start : start + self._EXT_QUOTES_CHUNK]
            try:
                data = await self._aget_data(
                    "/v2/stocks/snapshots",
                    params={"symbols": ",".join(chunk), "feed": self._data_feed},
                )
            except Exception:
                logger.warning(
                    "Alpaca day-volume snapshot failed for %d symbols",
                    len(chunk),
                    exc_info=True,
                )
                continue
            for sym in chunk:
                snap = data.get(sym) if isinstance(data, dict) else None
                if not isinstance(snap, dict):
                    continue
                daily_bar = snap.get("dailyBar")
                if not isinstance(daily_bar, dict):
                    continue
                try:
                    volume = int(float(daily_bar.get("v")))
                except (TypeError, ValueError):
                    continue
                if volume <= 0:
                    continue
                ts = str(daily_bar.get("t") or snap.get("t") or "")
                out[sym] = (volume, ts)
        return out

    def get_stock_snapshot_fields(self, symbol: str) -> dict[str, float] | None:
        """Read-only equity snapshot: last price and today's daily-bar volume.

        Uses ``GET /v2/stocks/snapshots``. Fail-soft — returns None on error or
        missing fields; never raises.
        """
        sym = symbol.strip().upper()
        if not sym:
            return None
        try:
            data = self._get_data(
                "/v2/stocks/snapshots",
                params={"symbols": sym, "feed": self._data_feed},
            )
        except Exception:
            logger.warning(
                "Alpaca stock snapshot failed for %s", sym, exc_info=True
            )
            return None

        snap = data.get(sym) if isinstance(data, dict) else None
        if not isinstance(snap, dict):
            return None

        trade = snap.get("latestTrade") or {}
        quote = snap.get("latestQuote") or {}
        daily_bar = snap.get("dailyBar") or {}

        def _f(v: object) -> float | None:
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        last_price = _f(trade.get("p"))
        if last_price is None:
            bid = _f(quote.get("bp"))
            ask = _f(quote.get("ap"))
            if bid is not None and ask is not None:
                last_price = (bid + ask) / 2.0
            elif bid is not None:
                last_price = bid
            elif ask is not None:
                last_price = ask

        day_volume = _f(daily_bar.get("v"))
        if last_price is None or day_volume is None or day_volume <= 0:
            return None
        return {"last_price": last_price, "day_volume": day_volume}

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

    # --- Options-flow screener (read-only data + contracts metadata) -------------

    _OPTIONS_BARS_CHUNK = 100
    _OPTIONS_BARS_MAX_PAGES = 50
    _OPTIONS_TRADES_CHUNK = 100
    _OPTIONS_TRADES_MAX_PAGES = 50
    _OPTIONS_QUOTES_CHUNK = 100
    _OPTIONS_CONTRACTS_MAX_PAGES = 50

    def get_option_bars(
        self,
        occ_symbols: list[str],
        start: str,
        end: str | None = None,
        timeframe: str = "1Day",
    ) -> dict[str, list[dict]]:
        """Historical option bars (data API). Fail-soft ``{}`` on any error.

        ``GET /v1beta1/options/bars?symbols=<csv>&timeframe=…&start=…``
        Chunks ≤100 symbols/request; paginates via ``next_page_token``.
        """
        symbols = [s.strip().upper() for s in occ_symbols if s and str(s).strip()]
        if not symbols or not start:
            return {}
        out: dict[str, list[dict]] = {}
        try:
            for i in range(0, len(symbols), self._OPTIONS_BARS_CHUNK):
                chunk = symbols[i : i + self._OPTIONS_BARS_CHUNK]
                page_token: str | None = None
                for _ in range(self._OPTIONS_BARS_MAX_PAGES):
                    params: dict[str, Any] = {
                        "symbols": ",".join(chunk),
                        "timeframe": timeframe,
                        "start": start,
                    }
                    if end:
                        params["end"] = end
                    if page_token:
                        params["page_token"] = page_token
                    data = self._get_data("/v1beta1/options/bars", params=params)
                    if not isinstance(data, dict):
                        break
                    bars = data.get("bars") or {}
                    if isinstance(bars, dict):
                        for occ, items in bars.items():
                            if not isinstance(items, list):
                                continue
                            bucket = out.setdefault(str(occ), [])
                            for bar in items:
                                if not isinstance(bar, dict):
                                    continue
                                bucket.append(
                                    {
                                        "t": bar.get("t"),
                                        "o": bar.get("o"),
                                        "h": bar.get("h"),
                                        "l": bar.get("l"),
                                        "c": bar.get("c"),
                                        "v": bar.get("v"),
                                        "n": bar.get("n"),
                                    }
                                )
                    page_token = data.get("next_page_token")
                    if not page_token:
                        break
        except Exception:
            return {}
        return out

    def get_option_trades(
        self,
        occ_symbols: list[str],
        *,
        start: str,
        end: str | None = None,
        limit: int = 10_000,
    ) -> dict[str, list[dict]]:
        """Historical option trades (data API). Fail-soft ``{}`` on any error.

        ``GET /v1beta1/options/trades?symbols=<csv>&start=<RFC3339>&limit=…``
        Chunks ≤100 symbols/request; paginates via ``next_page_token``.
        """
        symbols = [s.strip().upper() for s in occ_symbols if s and str(s).strip()]
        if not symbols or not start:
            return {}
        out: dict[str, list[dict]] = {}
        try:
            for i in range(0, len(symbols), self._OPTIONS_TRADES_CHUNK):
                chunk = symbols[i : i + self._OPTIONS_TRADES_CHUNK]
                page_token: str | None = None
                for _ in range(self._OPTIONS_TRADES_MAX_PAGES):
                    params: dict[str, Any] = {
                        "symbols": ",".join(chunk),
                        "start": start,
                        "limit": int(limit),
                    }
                    if end:
                        params["end"] = end
                    if page_token:
                        params["page_token"] = page_token
                    data = self._get_data("/v1beta1/options/trades", params=params)
                    if not isinstance(data, dict):
                        break
                    trades = data.get("trades") or {}
                    if isinstance(trades, dict):
                        for occ, items in trades.items():
                            if not isinstance(items, list):
                                continue
                            bucket = out.setdefault(str(occ), [])
                            for trade in items:
                                if not isinstance(trade, dict):
                                    continue
                                bucket.append(
                                    {
                                        "t": trade.get("t"),
                                        "p": trade.get("p"),
                                        "s": trade.get("s"),
                                        "x": trade.get("x"),
                                        "c": trade.get("c"),
                                    }
                                )
                    page_token = data.get("next_page_token")
                    if not page_token:
                        break
        except Exception:
            return {}
        return out

    def get_option_latest_quotes(
        self, occ_symbols: list[str]
    ) -> dict[str, dict]:
        """Latest option quotes (data API). Fail-soft ``{}`` on any error.

        ``GET /v1beta1/options/quotes/latest?symbols=<csv>&feed=opra``
        Returns ``{occ: {"bid": float|None, "ask": float|None, "ts": str|None}}``.
        """
        symbols = [s.strip().upper() for s in occ_symbols if s and str(s).strip()]
        if not symbols:
            return {}
        out: dict[str, dict] = {}
        try:
            for i in range(0, len(symbols), self._OPTIONS_QUOTES_CHUNK):
                chunk = symbols[i : i + self._OPTIONS_QUOTES_CHUNK]
                data = self._get_data(
                    "/v1beta1/options/quotes/latest",
                    params={"symbols": ",".join(chunk), "feed": "opra"},
                )
                if not isinstance(data, dict):
                    continue
                quotes = data.get("quotes") or {}
                if not isinstance(quotes, dict):
                    continue
                for occ, raw in quotes.items():
                    if not isinstance(raw, dict):
                        continue
                    bid: float | None = None
                    ask: float | None = None
                    try:
                        bp = raw.get("bp")
                        if bp is not None:
                            b = float(bp)
                            if b > 0:
                                bid = b
                    except (TypeError, ValueError):
                        pass
                    try:
                        ap = raw.get("ap")
                        if ap is not None:
                            a = float(ap)
                            if a > 0:
                                ask = a
                    except (TypeError, ValueError):
                        pass
                    ts = raw.get("t")
                    ts_out = str(ts).strip() if ts else None
                    out[str(occ).strip().upper()] = {
                        "bid": bid,
                        "ask": ask,
                        "ts": ts_out,
                    }
        except Exception:
            return {}
        return out

    def get_option_snapshots(
        self, occ_symbols: list[str]
    ) -> dict[str, dict]:
        """Option snapshots (data API). Fail-soft ``{}`` on any error.

        ``GET /v1beta1/options/snapshots?symbols=<csv>&feed=opra``
        Returns ``{occ: snapshot}`` with vendor keys (``latestQuote``,
        ``latestTrade``, ``greeks``, ``impliedVolatility``, ``openInterest``, …).
        """
        symbols = [s.strip().upper() for s in occ_symbols if s and str(s).strip()]
        if not symbols:
            return {}
        out: dict[str, dict] = {}
        try:
            for i in range(0, len(symbols), self._OPTIONS_QUOTES_CHUNK):
                chunk = symbols[i : i + self._OPTIONS_QUOTES_CHUNK]
                data = self._get_data(
                    "/v1beta1/options/snapshots",
                    params={"symbols": ",".join(chunk), "feed": "opra"},
                )
                if not isinstance(data, dict):
                    continue
                snapshots = data.get("snapshots") or {}
                if not isinstance(snapshots, dict):
                    continue
                for occ, raw in snapshots.items():
                    if isinstance(raw, dict):
                        out[str(occ).strip().upper()] = raw
        except Exception:
            return {}
        return out

    def get_option_trade_conditions(self) -> dict[str, str]:
        """OPRA trade condition code → description mapping. Fail-soft ``{}``."""
        try:
            data = self._get_data("/v1beta1/options/meta/conditions/trade")
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, str] = {}
        for code, desc in data.items():
            if code is None:
                continue
            out[str(code)] = str(desc) if desc is not None else ""
        return out

    def get_option_contracts(
        self,
        underlying: str,
        *,
        expiration_date_gte: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read-only trading-API contracts metadata (OI). Fail-soft ``[]``.

        ``GET /v2/options/contracts?underlying_symbols=…`` — the ONLY trading-API
        call used by the options-flow screener; metadata only, never an order path.
        """
        sym = underlying.strip().upper()
        if not sym:
            return []
        out: list[dict] = []
        page_token: str | None = None
        try:
            for _ in range(self._OPTIONS_CONTRACTS_MAX_PAGES):
                params: dict[str, Any] = {
                    "underlying_symbols": sym,
                    "limit": int(limit),
                }
                if expiration_date_gte:
                    params["expiration_date_gte"] = expiration_date_gte
                if page_token:
                    params["page_token"] = page_token
                data = self._request(
                    self._base_url, "/v2/options/contracts", params=params
                )
                if not isinstance(data, dict):
                    break
                contracts = data.get("option_contracts") or data.get("contracts") or []
                if not isinstance(contracts, list):
                    break
                for item in contracts:
                    if isinstance(item, dict):
                        out.append(item)
                page_token = data.get("next_page_token") or data.get("page_token")
                if not page_token:
                    break
        except Exception:
            return []
        return out

    def get_option_chain_snapshot(
        self,
        underlying: str,
        feed: str = "opra",
        *,
        expiration_date: str | None = None,
        expiration_date_gte: str | None = None,
        expiration_date_lte: str | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        limit: int | None = None,
        max_pages: int = 1,
    ) -> dict:
        """Chain option snapshots for an underlying. Fail-soft ``{}``.

        ``GET /v1beta1/options/snapshots/{underlying}?feed=opra`` — returns the
        merged ``snapshots`` dict. Defaults (no filter, one page) keep the
        original single-page behavior the poll's active-set relies on; the
        chain-viewer endpoint passes ``expiration_date`` + ``limit=1000`` +
        ``max_pages`` — the vendor page default is 100, alphabetical, which
        truncates to near-dated calls otherwise.

        The ``*_gte``/``*_lte`` range filters let a caller that only needs an ATM
        IV reading pull a slice instead of the board: an expiry window plus a
        strike band takes AAPL from 3,422 contracts to ~700 (measured 2026-07-25),
        which is what makes a wide breadth tier affordable. ⚠️ Paging is still
        alphabetical, so a dense chain fetched with ``max_pages=1`` comes back as
        near-dated contracts ONLY — SPY returned 7/10/11-DTE expiries and could
        not bracket 30 DTE at all. Range filters narrow, they do not reorder.
        """
        sym = underlying.strip().upper()
        if not sym:
            return {}
        out: dict = {}
        page_token: str | None = None
        try:
            for _ in range(max(1, max_pages)):
                params: dict[str, Any] = {"feed": feed}
                if expiration_date:
                    params["expiration_date"] = expiration_date
                if expiration_date_gte:
                    params["expiration_date_gte"] = expiration_date_gte
                if expiration_date_lte:
                    params["expiration_date_lte"] = expiration_date_lte
                if strike_price_gte is not None:
                    params["strike_price_gte"] = strike_price_gte
                if strike_price_lte is not None:
                    params["strike_price_lte"] = strike_price_lte
                if limit:
                    params["limit"] = int(limit)
                if page_token:
                    params["page_token"] = page_token
                data = self._get_data(
                    f"/v1beta1/options/snapshots/{sym}", params=params
                )
                if not isinstance(data, dict):
                    break
                snapshots = data.get("snapshots")
                if isinstance(snapshots, dict):
                    out.update(snapshots)
                page_token = data.get("next_page_token")
                if not page_token:
                    break
            else:
                if page_token:
                    # Stopping with pages left means the tail is dropped in the
                    # vendor's own alphabetical order — never do that silently.
                    logger.warning(
                        "Option chain snapshot capped at %d pages for %s "
                        "(%d contracts fetched, more available)",
                        max_pages,
                        sym,
                        len(out),
                    )
        except Exception:
            return out
        return out

    def get_news(
        self,
        symbols: list[str] | None = None,
        *,
        limit: int = 50,
        start: str | None = None,
    ) -> list[dict]:
        """Recent news items (Benzinga via ``/v1beta1/news``), newest-first.

        Fail-soft ``[]``. ``limit`` is capped at the vendor's 50-per-page max —
        callers needing history walk ``start`` themselves.
        """
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit), 50)),
            "sort": "desc",
        }
        if symbols:
            cleaned = sorted({s.strip().upper() for s in symbols if s and s.strip()})
            if cleaned:
                params["symbols"] = ",".join(cleaned)
        if start:
            params["start"] = start
        try:
            data = self._get_data("/v1beta1/news", params=params)
        except Exception:
            return []
        items = data.get("news") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
