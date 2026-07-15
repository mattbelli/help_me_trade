"""Local Alpaca-protocol proxy — hands the API hub its slice of the tape.

The stocks stream allows one connection per account, and the collector owns
it. The API's AlpacaMarketStreamHub still needs live trades + dailyBars for
the ~30-500 watchlist/position symbols, so this serves the SAME JSON protocol
on localhost: the hub's only change is the URL it dials
(EQUITY_TAPE_EXTERNAL_COLLECTOR flips brokers.market_stream.alpaca_market_ws_url).

Loopback-only listener; auth is accepted unconditionally (the hub still sends
its real keys — they never leave the box). Fan-out is queue-per-client with
drop-on-overflow: a wedged client loses ITS messages, never the hot path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import websockets

from signals.equity_stream.constants import (
    EQUITY_TAPE_PROXY_HOST,
    EQUITY_TAPE_PROXY_PORT,
    PROXY_CLIENT_QUEUE_MAX,
)
from signals.equity_stream.stream import parse_client_frame

logger = logging.getLogger(__name__)

_CHANNELS = ("trades", "quotes", "dailyBars")


class _Client:
    def __init__(self) -> None:
        self.subs: dict[str, set[str]] = {ch: set() for ch in _CHANNELS}
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=PROXY_CLIENT_QUEUE_MAX
        )
        self.drops = 0


class LocalMarketProxy:
    """websockets server + per-client subscription registry."""

    def __init__(
        self,
        *,
        host: str = EQUITY_TAPE_PROXY_HOST,
        port: int = EQUITY_TAPE_PROXY_PORT,
        on_daily_bar_symbols_change: Callable[[set[str]], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_daily_change = on_daily_bar_symbols_change
        self._clients: dict[Any, _Client] = {}
        # Union view per channel — the hot path asks wants() 17k times/s, so
        # membership must be one set lookup, rebuilt only on subscription edits.
        self._union: dict[str, set[str]] = {ch: set() for ch in _CHANNELS}

    # -- hot path ----------------------------------------------------------------

    def wants(self, channel: str, symbol: str) -> bool:
        return symbol in self._union[channel]

    def publish(self, channel: str, symbol: str, json_msg: dict[str, Any]) -> None:
        for client in self._clients.values():
            if symbol in client.subs[channel]:
                try:
                    client.queue.put_nowait(json_msg)
                except asyncio.QueueFull:
                    client.drops += 1

    # -- stats -------------------------------------------------------------------

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def total_drops(self) -> int:
        return sum(c.drops for c in self._clients.values())

    def union_size(self, channel: str) -> int:
        return len(self._union[channel])

    # -- server ------------------------------------------------------------------

    async def serve(self) -> None:
        async with websockets.serve(self._handle, self._host, self._port):
            logger.info(
                "local market proxy listening on ws://%s:%d", self._host, self._port
            )
            await asyncio.Future()

    def _rebuild_unions(self) -> None:
        for ch in _CHANNELS:
            self._union[ch] = set().union(
                *(c.subs[ch] for c in self._clients.values())
            ) if self._clients else set()
        if self._on_daily_change is not None:
            try:
                self._on_daily_change(set(self._union["dailyBars"]))
            except Exception:
                logger.warning("dailyBars change callback failed", exc_info=True)

    async def _handle(self, ws: Any) -> None:
        client = _Client()
        self._clients[ws] = client
        sender = asyncio.create_task(self._sender(ws, client))
        try:
            await ws.send(json.dumps([{"T": "success", "msg": "connected"}]))
            async for raw in ws:
                for msg in parse_client_frame(raw):
                    action = str(msg.get("action", "")).lower()
                    if action == "auth":
                        await ws.send(
                            json.dumps([{"T": "success", "msg": "authenticated"}])
                        )
                    elif action in ("subscribe", "unsubscribe"):
                        self._apply_subscription(client, action, msg)
                        await ws.send(json.dumps([self._ack(client)]))
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.pop(ws, None)
            sender.cancel()
            self._rebuild_unions()

    def _apply_subscription(
        self, client: _Client, action: str, msg: dict[str, Any]
    ) -> None:
        for ch in _CHANNELS:
            raw = msg.get(ch)
            if not isinstance(raw, list):
                continue
            symbols = {
                str(s).strip().upper() for s in raw if isinstance(s, str) and s.strip()
            }
            if action == "subscribe":
                client.subs[ch] |= symbols
            else:
                client.subs[ch] -= symbols
        self._rebuild_unions()

    @staticmethod
    def _ack(client: _Client) -> dict[str, Any]:
        return {
            "T": "subscription",
            **{ch: sorted(client.subs[ch]) for ch in _CHANNELS},
        }

    async def _sender(self, ws: Any, client: _Client) -> None:
        """Drain the client's queue; batch whatever is ready into one frame."""
        try:
            while True:
                first = await client.queue.get()
                batch = [first]
                while len(batch) < 128:
                    try:
                        batch.append(client.queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                await ws.send(json.dumps(batch))
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
