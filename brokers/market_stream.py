"""Alpaca market data WebSocket hub — one upstream connection, many browser clients."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect

from brokers.alpaca import AlpacaAdapter
from brokers.errors import BrokerError
from brokers.factory import get_broker
from config.settings import alpaca_config, broker_is_configured, normalize_alpaca_data_feed

logger = logging.getLogger(__name__)


def alpaca_market_ws_url(feed: str | None = None) -> str:
    resolved = normalize_alpaca_data_feed(
        feed if feed is not None else alpaca_config().get("data_feed", "iex")
    )
    return f"wss://stream.data.alpaca.markets/v2/{resolved}"
MAX_WS_SYMBOLS = 30
RECONNECT_BASE_SEC = 2.0
RECONNECT_MAX_SEC = 60.0


@dataclass
class QuoteState:
    price: float = 0.0
    change_pct: float = 0.0
    sparkline: list[float] = field(default_factory=list)
    prev_close: float = 0.0


class MarketStreamHub:
    """Shares a single Alpaca data stream across frontend WebSocket clients."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, set[str]] = {}
        self._pinned: set[str] = set()
        self._quotes: dict[str, QuoteState] = {}
        self._prev_prices: dict[str, float] = {}
        self._subscribed: set[str] = set()
        self._pending_add: set[str] = set()
        self._pending_remove: set[str] = set()
        self._lock = asyncio.Lock()
        self._upstream_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._upstream_task = asyncio.create_task(self._upstream_loop())

    async def stop(self) -> None:
        self._running = False
        if self._upstream_task:
            self._upstream_task.cancel()
            try:
                await self._upstream_task
            except asyncio.CancelledError:
                pass
            self._upstream_task = None

    async def handle_client(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients[ws] = set()
        try:
            await self._send_quotes(ws, self._quotes)
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json(
                        {"type": "error", "message": "Invalid JSON message"}
                    )
                    continue
                action = str(msg.get("action", "")).strip().lower()
                if action == "subscribe":
                    symbols = _normalize_symbols(msg.get("symbols"))
                    self._clients[ws] = symbols
                    await self._ensure_baselines(symbols)
                    await self._sync_upstream()
                    await self._send_quotes(ws, self._snapshot_for(symbols))
                elif action == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.pop(ws, None)
            await self._sync_upstream()

    async def _send_quotes(
        self, ws: WebSocket, quotes: dict[str, QuoteState]
    ) -> None:
        if not quotes:
            return
        await ws.send_json(
            {
                "type": "quotes",
                "live": broker_is_configured("alpaca"),
                "quotes": {sym: _quote_payload(state) for sym, state in quotes.items()},
            }
        )

    async def _broadcast_quote(self, symbol: str) -> None:
        state = self._quotes.get(symbol)
        if not state:
            return
        payload = {
            "type": "quote",
            "symbol": symbol,
            "live": True,
            **_quote_payload(state),
        }
        dead: list[WebSocket] = []
        for ws, symbols in list(self._clients.items()):
            if symbol not in symbols:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    def quote_snapshot(self, symbols: set[str] | list[str]) -> dict[str, dict[str, Any]]:
        normalized = {str(s).strip().upper() for s in symbols if str(s).strip()}
        return {
            sym: _quote_payload(self._quotes[sym])
            for sym in normalized
            if sym in self._quotes
        }

    async def broadcast_alert(self, payload: dict[str, Any]) -> None:
        """Push rule/alert payloads to all connected market WS clients."""
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json({"type": "alert", **payload})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    async def ensure_symbols(self, symbols: set[str]) -> None:
        normalized = {s.strip().upper() for s in symbols if s.strip()}
        if normalized == self._pinned:
            return
        self._pinned = normalized
        await self._sync_upstream()

    def _all_symbols(self) -> set[str]:
        out: set[str] = set(self._pinned)
        for symbols in self._clients.values():
            out.update(symbols)
        return out

    def _snapshot_for(self, symbols: set[str]) -> dict[str, QuoteState]:
        return {sym: self._quotes[sym] for sym in symbols if sym in self._quotes}

    async def _sync_upstream(self) -> None:
        desired = self._all_symbols()
        if len(desired) > MAX_WS_SYMBOLS:
            desired = set(sorted(desired)[:MAX_WS_SYMBOLS])
        async with self._lock:
            to_add = desired - self._subscribed
            to_remove = self._subscribed - desired
            self._subscribed = desired
            self._pending_add |= to_add
            self._pending_remove |= to_remove
        if to_add:
            await self._ensure_baselines(to_add)

    async def _ensure_baselines(self, symbols: set[str]) -> None:
        missing = [s for s in symbols if s not in self._quotes]
        if not missing or not broker_is_configured("alpaca"):
            return
        try:
            broker = get_broker("alpaca")
            if not isinstance(broker, AlpacaAdapter):
                return
            bars_by_symbol = await broker.aget_stock_bars_batch(missing, days=12)
        except BrokerError as exc:
            logger.warning("Watchlist baseline fetch failed: %s", exc)
            return

        for sym in missing:
            bars = bars_by_symbol.get(sym, [])
            if not bars:
                self._quotes[sym] = QuoteState()
                continue
            closes = [b.close for b in bars]
            price = closes[-1]
            prev = closes[-2] if len(closes) >= 2 else price
            change_pct = ((price - prev) / prev * 100) if prev else 0.0
            self._quotes[sym] = QuoteState(
                price=price,
                change_pct=round(change_pct, 2),
                sparkline=[round(c, 4) for c in closes[-7:]],
                prev_close=prev,
            )
            self._prev_prices[sym] = price

    async def _upstream_loop(self) -> None:
        backoff = RECONNECT_BASE_SEC

        while self._running:
            if not broker_is_configured("alpaca"):
                await asyncio.sleep(5)
                continue

            cfg = alpaca_config()
            key_id = cfg.get("api_key_id", "").strip()
            secret = cfg.get("api_secret_key", "").strip()
            if not key_id or not secret:
                await asyncio.sleep(5)
                continue

            try:
                async with websockets.connect(
                    alpaca_market_ws_url(cfg.get("data_feed")),
                    additional_headers={
                        "APCA-API-KEY-ID": key_id,
                        "APCA-API-SECRET-KEY": secret,
                    },
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                ) as alpaca_ws:
                    await self._alpaca_handshake(alpaca_ws, key_id, secret)
                    backoff = RECONNECT_BASE_SEC
                    logger.info("Alpaca market stream connected")

                    async with self._lock:
                        initial = set(self._subscribed)
                        self._pending_add -= initial
                    if initial:
                        await self._alpaca_send(
                            alpaca_ws,
                            {
                                "action": "subscribe",
                                "trades": sorted(initial),
                                "dailyBars": sorted(initial),
                            },
                        )

                    while self._running:
                        if self._pending_add or self._pending_remove:
                            async with self._lock:
                                pending_remove = set(self._pending_remove)
                                pending_add = set(self._pending_add)
                                self._pending_remove.clear()
                                self._pending_add.clear()
                            if pending_remove:
                                await self._alpaca_send(
                                    alpaca_ws,
                                    {
                                        "action": "unsubscribe",
                                        "trades": sorted(pending_remove),
                                        "dailyBars": sorted(pending_remove),
                                    },
                                )
                            if pending_add:
                                await self._alpaca_send(
                                    alpaca_ws,
                                    {
                                        "action": "subscribe",
                                        "trades": sorted(pending_add),
                                        "dailyBars": sorted(pending_add),
                                    },
                                )

                        try:
                            raw = await asyncio.wait_for(
                                alpaca_ws.recv(), timeout=1.0
                            )
                        except asyncio.TimeoutError:
                            continue

                        await self._handle_alpaca_message(raw)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Alpaca market stream error (%s); reconnecting in %.0fs",
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SEC)

    async def _alpaca_handshake(
        self, ws: Any, key_id: str, secret: str
    ) -> None:
        """Consume connected, authenticate, and wait for authenticated."""
        auth_sent = False
        deadline = time.monotonic() + 12.0

        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            items = _parse_alpaca_frame(raw)

            for item in items:
                msg_type = item.get("T")
                if msg_type == "error":
                    code = item.get("code")
                    msg = str(item.get("msg", "Alpaca stream error"))
                    if code == 403 and "authenticated" in msg.lower():
                        return
                    raise BrokerError(msg)
                if msg_type == "success" and item.get("msg") == "authenticated":
                    return
                if msg_type == "success" and item.get("msg") == "connected":
                    if not auth_sent:
                        await self._alpaca_send(
                            ws,
                            {"action": "auth", "key": key_id, "secret": secret},
                        )
                        auth_sent = True

            if auth_sent:
                continue

        if not auth_sent:
            await self._alpaca_send(
                ws, {"action": "auth", "key": key_id, "secret": secret}
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            if _frame_has_success(raw, "authenticated"):
                return
            logger.warning(
                "Alpaca auth fallback response: %s",
                raw[:500] if isinstance(raw, str) else raw,
            )

        raise BrokerError("Alpaca stream did not return authenticated")

    async def _alpaca_send(self, ws: Any, payload: dict[str, Any]) -> None:
        await ws.send(json.dumps(payload))

    async def _handle_alpaca_message(self, raw: str) -> None:
        for item in _parse_alpaca_frame(raw):
            msg_type = item.get("T")
            if msg_type == "t":
                await self._on_trade(item)
            elif msg_type == "db":
                await self._on_daily_bar(item)

    async def _on_trade(self, item: dict[str, Any]) -> None:
        symbol = str(item.get("S", "")).upper()
        price = item.get("p")
        if not symbol or price is None:
            return
        price_f = float(price)
        prev = self._prev_prices.get(symbol)
        state = self._quotes.get(symbol)
        if not state:
            state = QuoteState(price=price_f, prev_close=price_f)
            self._quotes[symbol] = state
        state.price = price_f
        if state.prev_close:
            state.change_pct = round(
                (price_f - state.prev_close) / state.prev_close * 100, 2
            )
        if state.sparkline:
            state.sparkline = [*state.sparkline[:-1], round(price_f, 4)]
        else:
            state.sparkline = [round(price_f, 4)]
        await self._broadcast_quote(symbol)

        if prev is not None and prev != price_f:
            await self._check_price_alerts(symbol, prev, price_f)
        self._prev_prices[symbol] = price_f

    async def _check_price_alerts(
        self, symbol: str, prev: float, current: float
    ) -> None:
        try:
            from api.alert_checker import check_alerts

            triggered = await asyncio.to_thread(
                check_alerts,
                {symbol: current},
                prev_quotes={symbol: prev},
            )
        except Exception as exc:
            logger.warning("Price alert check failed: %s", exc)
            return

        for alert in triggered:
            payload = {
                "type": "alert",
                "symbol": symbol,
                "price": current,
                "alert": alert,
            }
            dead: list[WebSocket] = []
            for ws in list(self._clients):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.pop(ws, None)

    async def _on_daily_bar(self, item: dict[str, Any]) -> None:
        symbol = str(item.get("S", "")).upper()
        close = item.get("c")
        if not symbol or close is None:
            return
        close_f = float(close)
        state = self._quotes.get(symbol)
        if not state:
            state = QuoteState()
            self._quotes[symbol] = state
        if state.sparkline:
            state.sparkline = [*state.sparkline[:-1], round(close_f, 4)]
        else:
            state.sparkline = [round(close_f, 4)]
        state.price = close_f
        if state.prev_close:
            state.change_pct = round(
                (close_f - state.prev_close) / state.prev_close * 100, 2
            )
        prev = self._prev_prices.get(symbol)
        if prev is not None and prev != close_f:
            await self._check_price_alerts(symbol, prev, close_f)
        self._prev_prices[symbol] = close_f
        await self._broadcast_quote(symbol)


def _parse_alpaca_frame(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _frame_has_success(raw: str, expected_msg: str) -> bool:
    for item in _parse_alpaca_frame(raw):
        if item.get("T") == "success" and item.get("msg") == expected_msg:
            return True
    return False


def _normalize_symbols(raw: Any) -> set[str]:
    if not isinstance(raw, list):
        return set()
    out: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            sym = item.strip().upper()
            if sym:
                out.add(sym)
    return out


def _quote_payload(state: QuoteState) -> dict[str, Any]:
    return {
        "price": state.price,
        "change_pct": state.change_pct,
        "sparkline": state.sparkline,
    }


market_hub = MarketStreamHub()
