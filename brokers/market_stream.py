"""Market data WebSocket hub — one upstream connection, many browser clients.

`BaseMarketHub` holds all broker-agnostic machinery: browser-client
register/subscribe/broadcast, the pending-add/remove diff logic, QuoteState,
and alert broadcasting.  Broker-specific subclasses override only
`_upstream_loop` and `_ensure_baselines`.

Concrete implementations:
  AlpacaMarketStreamHub — original Alpaca IEX/SIP websocket upstream (default)
  SchwabMarketStreamHub — Schwab StreamClient upstream (see schwab_market_stream.py)
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

# Suspect-print guard parameters (shared by the Schwab stream handlers and the
# REST apply_extended poll): a price outside ±TICK_BAND_PCT of the reference —
# the LAST ADMITTED price, seeded from the prior close — is "suspect" and held
# until corroborated within TICK_CONFIRM_TOL of the immediately preceding raw
# value. Referencing last-admitted (not prev_close) means a legitimately gapped
# stock re-centers the band after its one-tick confirmation and then flows.
TICK_BAND_PCT = 0.50
TICK_CONFIRM_TOL = 0.05
# Absolute backstop vs prev_close: the rolling last-admitted reference is
# self-corroborating, so a corrupt feed drifting in ≤5% steps could walk it
# arbitrarily far with every tick "admitting" (no freeze alarm would fire).
# Nothing real trades at 10x / 0.1x yesterday's close intraday without a
# corporate action — hold those prints unconditionally, corroborated or not.
TICK_ABS_CEILING = 10.0

# Per-symbol broadcast coalescing window. Schwab's level-one stream can fire
# 30–100 ticks/sec on a hot name; sending each one to the phone WS would burn
# battery (radio + JS render) for no human-visible benefit. We send the first
# tick immediately, then collapse any further ticks inside the window into a
# single trailing broadcast that carries the LATEST state. Net effective rate
# ceiling: ~10 Hz / symbol / client. Alerts are NOT throttled.
BROADCAST_THROTTLE_MS = 100.0


@dataclass
class QuoteState:
    price: float = 0.0
    change_pct: float = 0.0
    sparkline: list[float] = field(default_factory=list)
    prev_close: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    # Extended-hours fields — kept here so WS broadcasts carry the same
    # pre/post/overnight quote the REST `/api/watchlist/market` returns. The
    # Schwab level-one stream doesn't emit ext fields; they're updated via
    # `BaseMarketHub.apply_extended()` from the REST poller and ride along on
    # the next broadcast.
    ext_price: float | None = None
    ext_change_pct: float | None = None
    market_session: str | None = None


class BaseMarketHub:
    """Broker-agnostic hub: browser-client registry, broadcast, and pending-diff logic.

    Subclasses must implement:
      `_upstream_loop()` — drives the upstream connection
      `_ensure_baselines(symbols)` — seeds QuoteState for new symbols
    """

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
        self._recent_alerts: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=50
        )
        # Broadcast throttle bookkeeping (see BROADCAST_THROTTLE_MS):
        #   _last_broadcast_ms[sym] — monotonic-ms of the last actual send
        #   _pending_broadcast[sym] — the in-flight trailing broadcast (if any)
        self._last_broadcast_ms: dict[str, float] = {}
        self._pending_broadcast: dict[str, asyncio.Task] = {}
        # Last raw ext price seen per symbol by apply_extended — one-poll
        # corroboration state for the suspect-ext-print guard (see apply_extended).
        self._pending_ext: dict[str, float] = {}

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
        # Cancel any pending trailing broadcasts so they don't fire post-stop.
        for task in list(self._pending_broadcast.values()):
            task.cancel()
        self._pending_broadcast.clear()
        self._last_broadcast_ms.clear()

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
                "live": self._live_flag(),
                "quotes": {sym: _quote_payload(state) for sym, state in quotes.items()},
            }
        )

    def _live_flag(self) -> bool:
        """Return the 'live' bool sent in quote payloads.  Subclasses may override."""
        from config.settings import primary_broker
        return broker_is_configured(primary_broker())

    async def _broadcast_quote(self, symbol: str) -> None:
        """Throttled per-symbol fan-out.

        First tick in a window sends immediately; subsequent ticks inside the
        window collapse into one trailing send at the boundary that re-reads
        `self._quotes[symbol]` (so the latest price wins). Hot streams settle
        at ~10 Hz / symbol / client; bursts and idle ticks pass through.
        """
        now_ms = time.monotonic() * 1000
        last_ms = self._last_broadcast_ms.get(symbol, 0.0)
        elapsed = now_ms - last_ms

        if elapsed >= BROADCAST_THROTTLE_MS:
            self._last_broadcast_ms[symbol] = now_ms
            await self._do_broadcast_quote(symbol)
            return

        existing = self._pending_broadcast.get(symbol)
        if existing is not None and not existing.done():
            # Another trailing broadcast is already queued for this symbol;
            # it'll re-read state when it fires, so latest price wins anyway.
            return

        delay_s = (BROADCAST_THROTTLE_MS - elapsed) / 1000.0
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop running (defensive — shouldn't happen on the live path).
            self._last_broadcast_ms[symbol] = now_ms
            await self._do_broadcast_quote(symbol)
            return
        self._pending_broadcast[symbol] = loop.create_task(
            self._trailing_broadcast(symbol, delay_s)
        )

    async def _trailing_broadcast(self, symbol: str, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
            self._last_broadcast_ms[symbol] = time.monotonic() * 1000
            await self._do_broadcast_quote(symbol)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Trailing broadcast for %s failed", symbol, exc_info=True)
        finally:
            self._pending_broadcast.pop(symbol, None)

    async def _do_broadcast_quote(self, symbol: str) -> None:
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
        enriched = {
            "id": payload.get("id") or f"alert-{uuid.uuid4().hex[:12]}",
            "ts": payload.get("ts") or datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        self._recent_alerts.append(enriched)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json({"type": "alert", **enriched})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    def recent_alerts(self) -> list[dict[str, Any]]:
        return list(reversed(self._recent_alerts))

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
        """Seed QuoteState for new symbols.  Override in each subclass."""
        raise NotImplementedError

    async def _upstream_loop(self) -> None:
        """Drive the upstream broker connection.  Override in each subclass."""
        raise NotImplementedError

    async def apply_extended(
        self,
        symbol: str,
        *,
        ext_price: float | None,
        ext_change_pct: float | None,
        market_session: str | None,
        broadcast: bool = True,
    ) -> None:
        """Update a symbol's extended-hours fields and (optionally) broadcast.

        Called by the `/api/watchlist/market` REST poll and the server-side alert
        poll so the WS stream carries the same pre/post/overnight pricing the phone
        headlines — the Schwab level-one stream doesn't emit ext fields. Creates a
        QuoteState if none exists yet. Also drives price-alert evaluation off the
        extended price (see below).
        """
        sym = str(symbol).strip().upper()
        if not sym:
            return
        state = self._quotes.get(sym)
        if not state:
            state = QuoteState()
            self._quotes[sym] = state
        # Suspect-ext-print guard with ONE-POLL corroboration. Reference = the last
        # ADMITTED ext price (state.ext_price only updates on admitted polls), falling
        # back to the prior close before the first ext write — the natural
        # last-admitted analogue of the stream guard. An out-of-band ext price is HELD
        # on the first poll (WARNING) and admitted on the next poll if it corroborates
        # the held value within TICK_CONFIRM_TOL. Worst case for a real ±60% AH gap:
        # the ext price + its alert are delayed one poll cycle (~30–60s); a transient
        # bogus REST value is dropped and never reaches state.ext_price (feeds the
        # phone headline + off-hours alerts).
        if ext_price is not None:
            ref = state.ext_price if state.ext_price else state.prev_close
            pending = self._pending_ext.get(sym)
            self._pending_ext[sym] = ext_price
            if (
                ref
                and ref > 0
                and not (
                    ref * (1 - TICK_BAND_PCT)
                    <= ext_price
                    <= ref * (1 + TICK_BAND_PCT)
                )
                and (
                    pending is None
                    or abs(ext_price - pending) > ext_price * TICK_CONFIRM_TOL
                )
            ):
                logger.warning(
                    "Holding suspect %s ext price %.4f (ref %.2f, pending %s)",
                    sym,
                    ext_price,
                    ref,
                    f"{pending:.4f}" if pending is not None else "none",
                )
                return
        prev_ext = state.ext_price
        state.ext_price = ext_price
        state.ext_change_pct = ext_change_pct
        state.market_session = market_session
        if broadcast:
            await self._broadcast_quote(sym)
        # Drive price-alert evaluation off the extended/overnight price too. The
        # live stream is silent outside regular hours, so without this an alert set
        # on the chart would never fire overnight even though the price updates here.
        # Cross-detection needs a prior value and an actual move.
        if ext_price is not None and prev_ext is not None and prev_ext != ext_price:
            await self._check_price_alerts(sym, prev_ext, ext_price)

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
            target = float(alert.get("price", 0.0) or 0.0)
            direction = str(alert.get("direction") or "cross")
            note = str(alert.get("note") or "")
            from signals.notify import notify

            await notify(
                kind="price_alert",
                title=f"Price alert: {symbol} {direction} ${target:g}",
                body=(
                    f"{symbol} {direction} ${target:g} — now ${current:.2f}"
                    + (f" ({note})" if note else "")
                ),
                severity="info",
                ref_type="alert",
                ref_id=str(alert.get("id")),
                action_url=f"/watchlist/{symbol}",
                data={
                    "symbol": symbol,
                    "alert_id": alert.get("id"),
                    "target_price": target,
                    "trigger_price": current,
                    "direction": direction,
                    "note": note,
                },
            )


class AlpacaMarketStreamHub(BaseMarketHub):
    """Alpaca IEX/SIP websocket upstream — original behavior, unchanged."""

    def _live_flag(self) -> bool:
        return broker_is_configured("alpaca")

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


# ---------------------------------------------------------------------------
# Backward-compat alias: `market_hub` is now created in api/main.py via the
# factory.  This module-level instance is kept for code that imports it
# directly from brokers.market_stream (existing tests, etc.).
# ---------------------------------------------------------------------------
class MarketStreamHub(AlpacaMarketStreamHub):
    """Backward-compatible alias — new code should use AlpacaMarketStreamHub."""


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
        "bid": state.bid,
        "ask": state.ask,
        "volume": state.volume,
        "ext_price": state.ext_price,
        "ext_change_pct": state.ext_change_pct,
        "market_session": state.market_session,
    }


class _LiveHubProxy:
    """`market_hub` is imported (at import time) by the trading path — execution_service,
    fill_tracker, exit_engine, proposals — to broadcast alerts. But the LIVE hub that actually
    serves WS clients is created per-data-provider in api/main.py. Historically this module-level
    instance was a SEPARATE hub with no clients, so execution/fill/exit alerts never reached the
    browser. This proxy forwards every attribute (broadcast_alert, …) to the live hub once it's
    bound at startup, and falls back to a standalone hub when none is bound (tests / import-only).
    """

    def __init__(self) -> None:
        self._fallback: BaseMarketHub = MarketStreamHub()
        self._live: BaseMarketHub | None = None

    def bind(self, hub: BaseMarketHub) -> None:
        self._live = hub

    def __getattr__(self, name: str) -> Any:
        target = self._live if self._live is not None else self._fallback
        return getattr(target, name)


market_hub = _LiveHubProxy()
