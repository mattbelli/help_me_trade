"""Alpaca stocks v2 websocket client — one socket, the whole tape.

Subscribes trades["*"] + quotes["*"] (both wildcards ACKed on stocks v2,
measured 2026-07-15 — unlike OPRA, where quotes refuse the wildcard) plus a
dynamic per-symbol dailyBars set on behalf of proxy clients. msgpack transport;
timestamps arrive as msgpack.Timestamp ext objects.

Measured midday load: ~17k msgs/s combined ≈ 9% of one core fully decoded,
delivery lag p50 14ms. The unknown is burst behavior (open/close) — the stats
line logs lag percentiles so a fanout queue upstream (OPRA's did 57ms→6.3s at
the bell) is visible in the journal, not invisible staleness.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import msgpack
import websockets

logger = logging.getLogger(__name__)

STOCKS_WS_URL = "wss://stream.data.alpaca.markets/v2/sip"
RECONNECT_BASE_SEC = 1.0
RECONNECT_MAX_SEC = 30.0
LAG_SAMPLE_EVERY = 16


def ts_to_ms(ts: Any) -> float | None:
    """Epoch-ms from a stream timestamp (msgpack.Timestamp or RFC3339 str)."""
    if isinstance(ts, msgpack.Timestamp):
        return ts.to_unix() * 1000.0
    if isinstance(ts, str):
        try:
            s = ts[:26] + "+00:00" if ts.endswith("Z") else ts
            return datetime.fromisoformat(s).timestamp() * 1000.0
        except ValueError:
            return None
    return None


def ts_to_iso(ts: Any) -> str:
    """RFC3339 string from a stream timestamp; empty string if unparseable."""
    if isinstance(ts, msgpack.Timestamp):
        return datetime.fromtimestamp(ts.to_unix(), timezone.utc).isoformat()
    return str(ts or "")


def to_json_msg(m: dict[str, Any]) -> dict[str, Any]:
    """Copy of an upstream message with msgpack Timestamps as RFC3339 strings —
    what the API hub's JSON parser expects from real Alpaca."""
    out: dict[str, Any] = {}
    for k, v in m.items():
        if isinstance(v, msgpack.Timestamp):
            out[k] = datetime.fromtimestamp(v.to_unix(), timezone.utc).isoformat()
        else:
            out[k] = v
    return out


class EquityStream:
    """Reconnecting wildcard consumer; dispatches decoded messages to callbacks.

    Callbacks are synchronous and must be cheap — they run on the hot recv loop
    (the same contract as options_flow's stream)."""

    def __init__(
        self,
        *,
        key: str,
        secret: str,
        on_trade: Callable[[dict[str, Any], float], None],
        on_quote: Callable[[dict[str, Any], float], None],
        on_daily_bar: Callable[[dict[str, Any]], None] | None = None,
        url: str = STOCKS_WS_URL,
    ) -> None:
        self._key = key
        self._secret = secret
        self._url = url
        self._on_trade = on_trade
        self._on_quote = on_quote
        self._on_daily_bar = on_daily_bar
        self._daily_syms: set[str] = set()
        self._daily_dirty = False
        self._running = True
        # stats
        self.msgs: dict[str, int] = {}
        self.reconnects = 0
        self.vendor_errors = 0
        self._lag_samples: list[float] = []
        self._lag_tick = 0

    def stop(self) -> None:
        self._running = False

    def set_daily_bar_symbols(self, symbols: set[str]) -> None:
        wanted = {s.strip().upper() for s in symbols if s.strip()}
        if wanted != self._daily_syms:
            self._daily_syms = wanted
            self._daily_dirty = True

    def lag_percentiles(self) -> tuple[float, float]:
        """(p50, p95) of recent delivery lag in ms; resets the sample window."""
        samples = sorted(self._lag_samples)
        self._lag_samples = []
        if not samples:
            return (float("nan"), float("nan"))
        return (
            samples[len(samples) // 2],
            samples[min(len(samples) - 1, int(len(samples) * 0.95))],
        )

    async def run(self) -> None:
        backoff = RECONNECT_BASE_SEC
        while self._running:
            try:
                async with websockets.connect(
                    self._url, max_size=None, ping_interval=20, ping_timeout=20
                ) as ws:
                    await self._handshake(ws)
                    backoff = RECONNECT_BASE_SEC
                    sub: dict[str, Any] = {
                        "action": "subscribe",
                        "trades": ["*"],
                        "quotes": ["*"],
                    }
                    if self._daily_syms:
                        sub["dailyBars"] = sorted(self._daily_syms)
                    await ws.send(msgpack.packb(sub))
                    self._daily_dirty = False
                    applied_daily = set(self._daily_syms)
                    logger.info(
                        "equity stream connected: trades[*] quotes[*] dailyBars=%d",
                        len(applied_daily),
                    )
                    while self._running:
                        if self._daily_dirty:
                            applied_daily = await self._apply_daily_diff(
                                ws, applied_daily
                            )
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        self._dispatch(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._running:
                    break
                self.reconnects += 1
                logger.warning(
                    "equity stream error (%s); reconnecting in %.0fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SEC)

    async def _handshake(self, ws: Any) -> None:
        first = msgpack.unpackb(await ws.recv(), raw=False)
        if not any(m.get("T") == "success" for m in first):
            raise ConnectionError(f"unexpected hello: {first}")
        await ws.send(
            msgpack.packb({"action": "auth", "key": self._key, "secret": self._secret})
        )
        got = msgpack.unpackb(await ws.recv(), raw=False)
        for m in got:
            if m.get("T") == "success" and m.get("msg") == "authenticated":
                return
        raise ConnectionError(f"auth failed: {got}")

    async def _apply_daily_diff(self, ws: Any, applied: set[str]) -> set[str]:
        self._daily_dirty = False
        wanted = set(self._daily_syms)
        to_add = wanted - applied
        to_remove = applied - wanted
        if to_remove:
            await ws.send(
                msgpack.packb(
                    {"action": "unsubscribe", "dailyBars": sorted(to_remove)}
                )
            )
        if to_add:
            await ws.send(
                msgpack.packb({"action": "subscribe", "dailyBars": sorted(to_add)})
            )
        return wanted

    def _dispatch(self, raw: bytes) -> None:
        now_ms = time.time() * 1000.0
        try:
            batch = msgpack.unpackb(raw, raw=False)
        except Exception:
            self.msgs["undecodable"] = self.msgs.get("undecodable", 0) + 1
            return
        for m in batch:
            t = m.get("T", "?")
            self.msgs[t] = self.msgs.get(t, 0) + 1
            if t == "t":
                self._sample_lag(m, now_ms)
                self._on_trade(m, now_ms)
            elif t == "q":
                self._sample_lag(m, now_ms)
                self._on_quote(m, now_ms)
            elif t in ("d", "db") and self._on_daily_bar is not None:
                self._on_daily_bar(m)
            elif t == "error":
                self.vendor_errors += 1
                logger.warning("equity stream vendor error frame: %s", m)
            elif t == "subscription":
                logger.info(
                    "equity stream subscription ack: trades=%s quotes=%s dailyBars=%d",
                    m.get("trades"),
                    m.get("quotes"),
                    len(m.get("dailyBars") or []),
                )

    def _sample_lag(self, m: dict[str, Any], now_ms: float) -> None:
        self._lag_tick += 1
        if self._lag_tick % LAG_SAMPLE_EVERY:
            return
        ts_ms = ts_to_ms(m.get("t"))
        if ts_ms is not None and len(self._lag_samples) < 8192:
            self._lag_samples.append(now_ms - ts_ms)


def parse_client_frame(raw: str | bytes) -> list[dict[str, Any]]:
    """Parse one JSON frame from a proxy client (the API hub sends JSON)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []
