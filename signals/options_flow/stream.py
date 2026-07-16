"""OPRA msgpack websocket client + subscription manager (design §5/§7).

Transport only: connect to Alpaca OPRA, manage trade/quote subscriptions, normalize
prints via ``intraday.normalize_print``, cache quotes. Detection (SWP-1, DIR-1) is
round 3b. Probe facts: ``research/options_flow/ws_probe_2026-07-09.md``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import msgpack
import websockets

from signals.options_flow.constants import (
    HOT_QUOTE_MAX,
    HOT_QUOTE_TTL_SECONDS,
    OPRA_QUOTE_SUB_CAP,
    QUOTE_ANCHOR_BUDGET,
    QUOTE_STALE_FOR_DIR1_SECONDS,
    STREAM_TRADES_MAX,
)
from signals.options_flow.intraday import normalize_print

logger = logging.getLogger(__name__)

OPRA_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/opra"

SUB_REFRESH_SECONDS = 300
ACK_SETTLE_SECONDS = 10.0  # grace for a refresh's own subscription echoes to land
PRINT_FLUSH_SECONDS = 1.0
QUOTE_SUB_DEBOUNCE_SECONDS = 5.0
HOUSEKEEPING_TICK_SECONDS = 0.05
LAG_SAMPLE_EVERY = 16

RECONNECT_BASE_SEC = 5.0
RECONNECT_MAX_SEC = 120.0
RECONNECT_HEALTHY_RESET_SEC = 60.0
CONNECTION_LIMIT_BACKOFF_SEC = 300.0
CONNECTION_LIMIT_LOG_INTERVAL_SEC = 3600.0

QUOTE_CACHE_MAX_AGE_MS = 3_600_000
_MS_PER_SEC = 1000


class ConnectionLimitExceeded(Exception):
    """Alpaca OPRA websocket auth returned code 406."""


def _unpack_frame(raw: bytes | str) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        return []
    # timestamp=2 keeps timestamps as int epoch-nanoseconds — no per-message
    # datetime objects on the hot path (quotes need only epoch-ms; ISO strings
    # are built lazily for the few prints that survive the meta gate).
    data = msgpack.unpackb(raw, timestamp=2)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _timestamp_to_iso(t: Any) -> str:
    if isinstance(t, int):
        dt = datetime.fromtimestamp(t / 1_000_000_000, tz=timezone.utc)
    elif isinstance(t, datetime):
        dt = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    elif isinstance(t, msgpack.ext.Timestamp):
        dt = datetime.fromtimestamp(t.to_unix(), tz=timezone.utc)
    elif isinstance(t, str):
        return t
    else:
        return str(t)
    ms = int(dt.microsecond / 1000)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _timestamp_to_ms(t: Any) -> int | None:
    if isinstance(t, int):
        return t // 1_000_000
    if isinstance(t, datetime):
        dt = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    if isinstance(t, msgpack.ext.Timestamp):
        return int(t.to_unix() * 1000)
    if isinstance(t, str):
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    return None


async def _default_connect() -> Any:
    return await websockets.connect(OPRA_WS_URL, max_size=2**23)


class OptionsFlowStream:
    """OPRA websocket transport: subscriptions, quote cache, normalized print batches."""

    def __init__(
        self,
        key: str,
        secret: str,
        on_prints: Callable[[list[dict]], None],
        get_subscription_set: Callable[[], list[str]],
        meta_for: Callable[[str], dict | None],
        *,
        connect_factory: Callable[[], Any] | None = None,
        get_anchor_quotes: Callable[[], list[str]] | None = None,
    ) -> None:
        self._key = key.strip()
        self._secret = secret.strip()
        self._on_prints = on_prints
        self._get_subscription_set = get_subscription_set
        self._meta_for = meta_for
        self._get_anchor_quotes = get_anchor_quotes
        self._connect_factory = connect_factory or _default_connect

        self._stopped = False
        self._ws: Any | None = None

        self._subscribed_trades: set[str] = set()
        self._subscribed_quotes: set[str] = set()
        # Server-confirmed state from "subscription" echo frames; None until the
        # first echo of a connection. _subscribed_* is what we *sent*, this is
        # what the server *acked* — the 2026-07-14 open-blindness bug lived in
        # exactly that gap, so divergence is surfaced instead of assumed away.
        self._acked_trades: set[str] | None = None
        self._acked_quotes: set[str] | None = None
        self._quote_cache: dict[str, tuple[float, float, int]] = {}
        self._quote_ring: dict[str, deque[tuple[int, float, float]]] = {}
        self._hot_print_ms: dict[str, int] = {}
        self._pending_prints: list[dict] = []
        self._pending_quote_adds: set[str] = set()

        self._last_flush_mono = 0.0
        self._last_quote_sub_mono = 0.0
        self._last_sub_refresh_mono = 0.0
        self._last_stats_log_mono = 0.0
        self._last_406_log_mono = 0.0

        self.msgs = 0
        self.prints = 0
        self.skipped_no_meta = 0
        self.reconnects = 0
        self.collected_prints: deque[dict] = deque(maxlen=5000)

        self._trade_seq = 0
        self._lag_samples_ms: deque[int] = deque(maxlen=256)

    def stop(self) -> None:
        self._stopped = True
        ws = self._ws
        if ws is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._close_ws(ws))

    async def _close_ws(self, ws: Any) -> None:
        try:
            close = ws.close
            if asyncio.iscoroutinefunction(close):
                await close()
            else:
                close()
        except Exception:
            logger.debug("options_flow stream: ws close failed", exc_info=True)

    def latest_quote(self, occ: str) -> tuple[float, float, int] | None:
        return self._quote_cache.get(occ.strip().upper())

    @property
    def is_market_wide(self) -> bool:
        """True while the trades tape is the ``*`` wildcard (vs the capped set)."""
        return "*" in self._subscribed_trades

    def quote_at(self, occ: str, ts_ms: int) -> tuple[float, float, int] | None:
        """Quote prevailing at print time: newest ring entry at-or-before ``ts_ms``."""
        sym = occ.strip().upper()
        ring = self._quote_ring.get(sym)
        if not ring:
            return None
        stale_ms = QUOTE_STALE_FOR_DIR1_SECONDS * _MS_PER_SEC
        after_ms = _MS_PER_SEC
        best_before_ts = -1
        best_before: tuple[float, float, int] | None = None
        earliest_after_ts: int | None = None
        earliest_after: tuple[float, float, int] | None = None
        for entry_ts, bid, ask in ring:
            if entry_ts <= ts_ms:
                if entry_ts > best_before_ts and ts_ms - entry_ts <= stale_ms:
                    best_before_ts = entry_ts
                    best_before = (bid, ask, entry_ts)
            elif entry_ts - ts_ms <= after_ms:
                if earliest_after_ts is None or entry_ts < earliest_after_ts:
                    earliest_after_ts = entry_ts
                    earliest_after = (bid, ask, entry_ts)
        if best_before is not None:
            return best_before
        return earliest_after

    async def run(self) -> None:
        backoff = RECONNECT_BASE_SEC

        while not self._stopped:
            ws: Any | None = None
            hk_task: asyncio.Task | None = None
            healthy_since: float | None = None
            try:
                ws = await self._connect_factory()
                self._ws = ws
                await self._handshake(ws)
                healthy_since = time.monotonic()
                self._last_sub_refresh_mono = 0.0
                await self._refresh_subscriptions(ws, force=True)

                # Housekeeping (flush/refresh/stats) runs on its own tick so the
                # recv loop pays no per-message wait_for task/timer overhead —
                # that plumbing alone was a large share of quote-CPU at
                # ~1,600 msg/s. stop() closes the socket, which unblocks recv.
                hk_task = asyncio.create_task(self._housekeeping_loop(ws))

                while not self._stopped:
                    raw = await ws.recv()
                    self._handle_frame(raw)

            except ConnectionLimitExceeded:
                now_mono = time.monotonic()
                if now_mono - self._last_406_log_mono >= CONNECTION_LIMIT_LOG_INTERVAL_SEC:
                    logger.warning(
                        "options_flow stream: connection limit exceeded (406); "
                        "backing off %.0fs",
                        CONNECTION_LIMIT_BACKOFF_SEC,
                    )
                    self._last_406_log_mono = now_mono
                self.reconnects += 1
                await self._sleep(CONNECTION_LIMIT_BACKOFF_SEC)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._stopped:
                    self.reconnects += 1
                    if (
                        healthy_since is not None
                        and time.monotonic() - healthy_since
                        >= RECONNECT_HEALTHY_RESET_SEC
                    ):
                        backoff = RECONNECT_BASE_SEC
                    logger.warning(
                        "options_flow stream error (%s); reconnecting in %.0fs",
                        exc,
                        backoff,
                    )
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, RECONNECT_MAX_SEC)
            finally:
                if hk_task is not None:
                    hk_task.cancel()
                    try:
                        await hk_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._ws = None
                # Server-side subscriptions die with the connection; forget them
                # or the next connect's diff computes "nothing to add" and the
                # stream silently receives no data after a reconnect.
                self._subscribed_trades = set()
                self._subscribed_quotes = set()
                self._acked_trades = None
                self._acked_quotes = None
                if ws is not None:
                    try:
                        close = ws.close
                        if asyncio.iscoroutinefunction(close):
                            await close()
                        else:
                            close()
                    except Exception:
                        pass

        await self._flush_prints(force=True)

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _housekeeping_loop(self, ws: Any) -> None:
        """Periodic flush/refresh/stats beside the recv loop; one connection each.

        A failure here (e.g. subscribe on a dying socket) recycles the connection
        by closing it — recv then raises and run()'s reconnect path takes over.
        """
        try:
            while not self._stopped:
                await asyncio.sleep(HOUSEKEEPING_TICK_SECONDS)
                now_mono = time.monotonic()
                await self._maybe_refresh_subscriptions(ws, now_mono)
                await self._maybe_flush_prints(now_mono)
                await self._maybe_apply_quote_subs(ws, now_mono)
                self._maybe_log_stats(now_mono)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "options_flow stream: housekeeping failed; recycling connection",
                exc_info=True,
            )
            await self._close_ws(ws)

    async def _handshake(self, ws: Any) -> None:
        auth_sent = False
        deadline = time.monotonic() + 12.0

        while time.monotonic() < deadline and not self._stopped:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            for item in _unpack_frame(raw):
                msg_type = item.get("T")
                if msg_type == "error":
                    code = item.get("code")
                    msg = str(item.get("msg", "OPRA stream error"))
                    if code == 406:
                        raise ConnectionLimitExceeded(msg)
                    raise RuntimeError(msg)
                if msg_type == "success" and item.get("msg") == "authenticated":
                    return
                if msg_type == "success" and item.get("msg") == "connected":
                    if not auth_sent:
                        await self._send(
                            ws, {"action": "auth", "key": self._key, "secret": self._secret}
                        )
                        auth_sent = True

        if not auth_sent:
            await self._send(
                ws, {"action": "auth", "key": self._key, "secret": self._secret}
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            for item in _unpack_frame(raw):
                if item.get("T") == "success" and item.get("msg") == "authenticated":
                    return
                if item.get("T") == "error" and item.get("code") == 406:
                    raise ConnectionLimitExceeded(str(item.get("msg", "")))

        raise RuntimeError("OPRA stream did not return authenticated")

    async def _send(self, ws: Any, payload: dict[str, Any]) -> None:
        await ws.send(msgpack.packb(payload))

    def _desired_trades(self) -> set[str]:
        raw = self._get_subscription_set() or []
        clipped = [str(s).strip().upper() for s in raw if str(s).strip()][:STREAM_TRADES_MAX]
        return set(clipped)

    def _desired_hot_quotes(self, now_ms: int) -> set[str]:
        ttl_ms = HOT_QUOTE_TTL_SECONDS * _MS_PER_SEC
        active = {
            occ: ts
            for occ, ts in self._hot_print_ms.items()
            if now_ms - ts <= ttl_ms
        }
        if len(active) <= HOT_QUOTE_MAX:
            return set(active.keys())
        ranked = sorted(active.items(), key=lambda kv: kv[1], reverse=True)
        return {occ for occ, _ in ranked[:HOT_QUOTE_MAX]}

    def _desired_quotes(self, now_ms: int, extra_hot: set[str] | None = None) -> set[str]:
        """Anchors + the reactive hot pool, clipped to the measured vendor cap.

        Anchors (standing, QUOTE_ANCHOR_BUDGET) are pre-positioned by the poll's
        dollar-volume ranking so the FIRST print on a hot contract classifies
        against a live quote; the hot pool (HOT_QUOTE_MAX, recency-ranked) chases
        whatever prints outside the anchors. ``extra_hot`` joins the recency
        ranking as if it printed now (the pending-escalation path). Anchors keep
        their slots when the union would exceed OPRA_QUOTE_SUB_CAP — the reactive
        tail is what gets trimmed.
        """
        anchors: list[str] = []
        if self._get_anchor_quotes is not None:
            try:
                anchors = [
                    str(s).strip().upper()
                    for s in (self._get_anchor_quotes() or [])
                    if str(s).strip()
                ]
            except Exception:
                logger.exception("options_flow stream: anchor quote getter failed")
        desired = set(anchors[:QUOTE_ANCHOR_BUDGET])

        ttl_ms = HOT_QUOTE_TTL_SECONDS * _MS_PER_SEC
        merged = {
            occ: ts
            for occ, ts in self._hot_print_ms.items()
            if now_ms - ts <= ttl_ms
        }
        for occ in extra_hot or ():
            # Pending adds are exempt from the TTL — they are pending precisely
            # because they just printed; rank them by their stamp when we have one.
            merged.setdefault(occ, self._hot_print_ms.get(occ, now_ms))
        hot_ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
        hot_added = 0
        for occ, _ts in hot_ranked:
            if hot_added >= HOT_QUOTE_MAX or len(desired) >= OPRA_QUOTE_SUB_CAP:
                break
            if occ not in desired:
                desired.add(occ)
                hot_added += 1
        return desired

    def _prune_quote_cache(self, now_ms: int) -> None:
        cutoff = now_ms - QUOTE_CACHE_MAX_AGE_MS
        stale = [occ for occ, (_, _, ts) in self._quote_cache.items() if ts < cutoff]
        for occ in stale:
            self._quote_cache.pop(occ, None)
            self._quote_ring.pop(occ, None)

    async def _diff_and_send(
        self,
        ws: Any,
        *,
        channel: str,
        current: set[str],
        desired: set[str],
    ) -> set[str]:
        to_add = sorted(desired - current)
        to_remove = sorted(current - desired)
        if to_remove:
            await self._send(ws, {"action": "unsubscribe", channel: to_remove})
            if channel == "quotes":
                for occ in to_remove:
                    self._quote_ring.pop(str(occ).strip().upper(), None)
        if to_add:
            await self._send(ws, {"action": "subscribe", channel: to_add})
        return desired

    async def _refresh_subscriptions(self, ws: Any, *, force: bool = False) -> None:
        now_mono = time.monotonic()
        if (
            not force
            and self._last_sub_refresh_mono > 0
            and now_mono - self._last_sub_refresh_mono < SUB_REFRESH_SECONDS
        ):
            return
        self._last_sub_refresh_mono = now_mono

        now_ms = int(time.time() * 1000)
        self._prune_quote_cache(now_ms)

        desired_trades = self._desired_trades()
        self._subscribed_trades = await self._diff_and_send(
            ws,
            channel="trades",
            current=self._subscribed_trades,
            desired=desired_trades,
        )

        desired_quotes = self._desired_quotes(now_ms)
        self._subscribed_quotes = await self._diff_and_send(
            ws,
            channel="quotes",
            current=self._subscribed_quotes,
            desired=desired_quotes,
        )
        self._pending_quote_adds -= self._subscribed_quotes

    async def _maybe_refresh_subscriptions(self, ws: Any, now_mono: float) -> None:
        if (
            self._last_sub_refresh_mono <= 0
            or now_mono - self._last_sub_refresh_mono >= SUB_REFRESH_SECONDS
        ):
            await self._refresh_subscriptions(ws)
            return
        # This cadence is anchored to connect time, not the market clock. The set turns
        # non-empty at the open, mid-window, so waiting for the next tick left us blind
        # for the remainder of it. Subscribe as soon as there is anything to subscribe to.
        if not self._subscribed_trades and self._desired_trades():
            await self._refresh_subscriptions(ws, force=True)

    async def _maybe_apply_quote_subs(self, ws: Any, now_mono: float) -> None:
        if not self._pending_quote_adds:
            return
        if now_mono - self._last_quote_sub_mono < QUOTE_SUB_DEBOUNCE_SECONDS:
            return
        now_ms = int(time.time() * 1000)
        desired = self._desired_quotes(now_ms, extra_hot=self._pending_quote_adds)
        self._pending_quote_adds.clear()
        self._last_quote_sub_mono = now_mono
        self._subscribed_quotes = await self._diff_and_send(
            ws,
            channel="quotes",
            current=self._subscribed_quotes,
            desired=desired,
        )

    async def _maybe_flush_prints(self, now_mono: float, *, force: bool = False) -> None:
        if not self._pending_prints:
            return
        if (
            not force
            and self._last_flush_mono > 0
            and now_mono - self._last_flush_mono < PRINT_FLUSH_SECONDS
        ):
            return
        await self._flush_prints(force=force)

    async def _flush_prints(self, *, force: bool = False) -> None:
        if not self._pending_prints:
            return
        batch = self._pending_prints
        self._pending_prints = []
        self._last_flush_mono = time.monotonic()
        try:
            self._on_prints(batch)
        except Exception:
            logger.exception("options_flow stream: on_prints callback failed")

    def _maybe_log_stats(self, now_mono: float) -> None:
        if (
            self._last_stats_log_mono > 0
            and now_mono - self._last_stats_log_mono < 60.0
        ):
            return
        self._last_stats_log_mono = now_mono
        lag_p50: int | str = "-"
        lag_p95: int | str = "-"
        if self._lag_samples_ms:
            ordered = sorted(self._lag_samples_ms)
            lag_p50 = ordered[len(ordered) // 2]
            lag_p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            self._lag_samples_ms.clear()
        logger.info(
            "options_flow stream stats: msgs=%s prints=%s skipped_no_meta=%s "
            "reconnects=%s trade_subs=%s quote_subs=%s ack_trades=%s ack_quotes=%s "
            "collected=%s lag_p50_ms=%s lag_p95_ms=%s",
            self.msgs,
            self.prints,
            self.skipped_no_meta,
            self.reconnects,
            len(self._subscribed_trades),
            len(self._subscribed_quotes),
            "-" if self._acked_trades is None else len(self._acked_trades),
            "-" if self._acked_quotes is None else len(self._acked_quotes),
            len(self.collected_prints),
            lag_p50,
            lag_p95,
        )
        # Quote subs churn between refreshes (hot-quote adds), so only trade
        # divergence is treated as an alarm; ACK_SETTLE skips the transient
        # window while a refresh's own echoes are still in flight.
        if (
            self._acked_trades is not None
            and self._acked_trades != self._subscribed_trades
            and now_mono - self._last_sub_refresh_mono >= ACK_SETTLE_SECONDS
        ):
            logger.warning(
                "options_flow stream: server-confirmed trade subs diverge from "
                "client state: sent=%s acked=%s missing=%s unexpected=%s — the "
                "stream may be silently missing flow",
                len(self._subscribed_trades),
                len(self._acked_trades),
                len(self._subscribed_trades - self._acked_trades),
                len(self._acked_trades - self._subscribed_trades),
            )

    def _handle_frame(self, raw: bytes | str) -> None:
        for item in _unpack_frame(raw):
            self.msgs += 1
            msg_type = item.get("T")
            if msg_type == "t":
                self._on_trade(item)
            elif msg_type == "q":
                self._on_quote(item)
            elif msg_type == "subscription":
                self._on_subscription_ack(item)
            elif msg_type == "error":
                logger.warning(
                    "options_flow stream: server error frame: code=%s msg=%s",
                    item.get("code"),
                    item.get("msg"),
                )

    def _on_subscription_ack(self, item: dict[str, Any]) -> None:
        # The server echoes its FULL current subscription state on every change.
        trades = item.get("trades")
        if isinstance(trades, list):
            self._acked_trades = {str(s).strip().upper() for s in trades}
        quotes = item.get("quotes")
        if isinstance(quotes, list):
            self._acked_quotes = {str(s).strip().upper() for s in quotes}

    def _on_trade(self, item: dict[str, Any]) -> None:
        occ = str(item.get("S") or "").strip().upper()
        if not occ:
            return

        ts_ms = _timestamp_to_ms(item.get("t"))
        if ts_ms is not None:
            self._hot_print_ms[occ] = ts_ms
            if occ not in self._subscribed_quotes:
                self._pending_quote_adds.add(occ)
            self._trade_seq += 1
            if self._trade_seq % LAG_SAMPLE_EVERY == 0:
                self._lag_samples_ms.append(int(time.time() * 1000) - ts_ms)

        meta = self._meta_for(occ)
        if meta is None:
            self.skipped_no_meta += 1
            return

        cond = item.get("c")
        conditions = [str(cond)] if cond else []
        raw = {
            "t": _timestamp_to_iso(item.get("t")),
            "p": item.get("p"),
            "s": item.get("s"),
            "x": item.get("x") or "",
            "c": conditions,
        }
        normalized = normalize_print(raw, meta=meta)
        if normalized is None:
            return
        self.prints += 1
        self._pending_prints.append(normalized)

    def _on_quote(self, item: dict[str, Any]) -> None:
        occ = str(item.get("S") or "").strip().upper()
        if not occ:
            return
        try:
            bid = float(item["bp"])
            ask = float(item["ap"])
        except (KeyError, TypeError, ValueError):
            return
        ts_ms = _timestamp_to_ms(item.get("t")) or int(time.time() * 1000)
        self._quote_cache[occ] = (bid, ask, ts_ms)
        ring = self._quote_ring.get(occ)
        if ring is None:
            ring = deque(maxlen=64)
            self._quote_ring[occ] = ring
        ring.append((ts_ms, bid, ask))
