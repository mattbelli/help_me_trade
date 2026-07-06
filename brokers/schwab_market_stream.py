"""Schwab StreamClient-backed market-data hub.

Replaces the Alpaca upstream when `MARKET_DATA_PROVIDER == "schwab"`.  All
browser-client machinery (register/subscribe/broadcast/fan-out) lives in
`BaseMarketHub`; this module overrides only the upstream loop and baselines.

Symbol routing:
  Slash-prefixed symbols (/ES, /NQ, /CL, …) → level-one futures stream.
  Everything else → level-one equity stream.

Reconnect strategy:
  Exponential backoff (2 s → 60 s).  A fresh `StreamClient` is created on
  every reconnect (an instance is dead after `logout()`).  The 30-second
  timeout on `handle_message()` detects silent drops (heartbeats every ~5-10 s).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from schwab.streaming import StreamClient

from brokers.errors import BrokerError
from brokers.market_clock import market_session
from brokers.market_stream import (
    RECONNECT_BASE_SEC,
    RECONNECT_MAX_SEC,
    BaseMarketHub,
    QuoteState,
)
from config.settings import broker_is_configured
from trading.market_hours import is_market_open

logger = logging.getLogger(__name__)

_HANDLE_MESSAGE_TIMEOUT = 30.0  # seconds before assuming the stream dropped

# A single tick >_MAX_TICK_RATIO× or <1/_MAX_TICK_RATIO× the official prior close is a bad
# print / crossed quote, not a real intraday move — drop it so it can't spike a chart candle
# or poison the watchlist / alerts / the RiskEngine's live quote.
_MAX_TICK_RATIO = 3.0

# Equity fields to request (minimise bandwidth)
_EQUITY_FIELDS = [
    StreamClient.LevelOneEquityFields.SYMBOL,
    StreamClient.LevelOneEquityFields.BID_PRICE,
    StreamClient.LevelOneEquityFields.ASK_PRICE,
    StreamClient.LevelOneEquityFields.LAST_PRICE,
    StreamClient.LevelOneEquityFields.TOTAL_VOLUME,
    StreamClient.LevelOneEquityFields.NET_CHANGE,
    StreamClient.LevelOneEquityFields.NET_CHANGE_PERCENT,
    StreamClient.LevelOneEquityFields.CLOSE_PRICE,
    StreamClient.LevelOneEquityFields.MARK,
]

# Futures fields to request
_FUTURES_FIELDS = [
    StreamClient.LevelOneFuturesFields.SYMBOL,
    StreamClient.LevelOneFuturesFields.BID_PRICE,
    StreamClient.LevelOneFuturesFields.ASK_PRICE,
    StreamClient.LevelOneFuturesFields.LAST_PRICE,
    StreamClient.LevelOneFuturesFields.TOTAL_VOLUME,
    StreamClient.LevelOneFuturesFields.NET_CHANGE,
    StreamClient.LevelOneFuturesFields.FUTURE_CHANGE_PERCENT,
    StreamClient.LevelOneFuturesFields.CLOSE_PRICE,
]

# schwab-py relabels each content dict from numeric keys to the field enum's
# MEMBER NAME before invoking our handler (StreamClient._Handler.label_message →
# BaseFieldEnum.relabel_message, str(value) -> name). So a tick arrives as
# {"key": "AAPL", "LAST_PRICE": 200.5, "BID_PRICE": ...} — we must read by
# `.name`, NOT `.value`. Reading `.value` (the int) returns None for every field,
# which silently drops every tick. These constants make the access key explicit.
_EQ = StreamClient.LevelOneEquityFields
_FUT = StreamClient.LevelOneFuturesFields


def _partition_symbols(symbols: set[str]) -> tuple[list[str], list[str]]:
    """Return (equities, futures) sorted lists from *symbols*.

    Futures are slash-prefixed (/ES, /NQ, /CL …); everything else is equity.
    """
    eq: list[str] = []
    fut: list[str] = []
    for s in sorted(symbols):
        (fut if s.startswith("/") else eq).append(s)
    return eq, fut


def _token_exists() -> bool:
    """Return True if the Schwab token file is present."""
    try:
        from brokers.schwab_auth import token_exists
        return token_exists()
    except Exception:
        return False


async def _build_stream_client() -> StreamClient:
    """Create and return a fresh Schwab StreamClient (blocking I/O off-thread)."""
    from brokers.schwab_auth import create_client

    client = await asyncio.to_thread(create_client, interactive=False)
    # account_id is vestigial in schwab-py 1.5.1 — omit it
    return StreamClient(client)


class SchwabMarketStreamHub(BaseMarketHub):
    """Market-data hub backed by Schwab level-one streaming quotes."""

    def __init__(self) -> None:
        super().__init__()
        # Hard references to in-flight per-tick tasks so the GC can't cancel
        # _broadcast_quote / _check_price_alerts mid-run (the cause of
        # "ticks reach the handler but seem to disappear" reports).
        self._inflight_ticks: set[asyncio.Task] = set()

    def _live_flag(self) -> bool:
        return broker_is_configured("schwab")

    # ------------------------------------------------------------------
    # Baselines: seed QuoteState from recent daily bars so the UI has
    # non-zero prices before the first stream tick arrives.
    # ------------------------------------------------------------------

    async def _ensure_baselines(self, symbols: set[str]) -> None:
        eq, fut = _partition_symbols(symbols)
        missing_eq = [s for s in eq if s not in self._quotes]
        missing_fut = [s for s in fut if s not in self._quotes]

        # Futures get an empty QuoteState; the first stream tick populates them.
        for sym in missing_fut:
            self._quotes[sym] = QuoteState()

        if not missing_eq:
            return

        # Try Schwab bars first; fall through to Alpaca on failure.
        bars_by_symbol: dict[str, list] = {}
        if broker_is_configured("schwab") and _token_exists():
            try:
                from brokers.schwab_market_data import SchwabMarketData

                md = await asyncio.to_thread(SchwabMarketData.from_settings)
                bars_by_symbol = await md.aget_stock_bars_batch(missing_eq, days=12)
            except Exception as exc:
                logger.warning(
                    "Schwab baseline fetch failed (%s); trying Alpaca fallback", exc
                )

        if not bars_by_symbol and broker_is_configured("alpaca"):
            try:
                from brokers.alpaca import AlpacaAdapter
                from brokers.factory import get_broker

                broker = get_broker("alpaca")
                if isinstance(broker, AlpacaAdapter):
                    bars_by_symbol = await broker.aget_stock_bars_batch(
                        missing_eq, days=12
                    )
            except BrokerError as exc:
                logger.warning("Alpaca baseline fallback failed: %s", exc)

        for sym in missing_eq:
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

    # ------------------------------------------------------------------
    # Upstream loop
    # ------------------------------------------------------------------

    async def _upstream_loop(self) -> None:
        backoff = RECONNECT_BASE_SEC

        while self._running:
            if not (broker_is_configured("schwab") and _token_exists()):
                await asyncio.sleep(5)
                continue

            stream: StreamClient | None = None
            try:
                stream = await _build_stream_client()

                # Register handlers BEFORE login
                stream.add_level_one_equity_handler(self._on_equity)
                stream.add_level_one_futures_handler(self._on_futures)

                await stream.login()
                backoff = RECONNECT_BASE_SEC
                logger.info("Schwab market stream connected")

                # Initial subscribe with current subscribed set
                async with self._lock:
                    initial = set(self._subscribed)
                    self._pending_add -= initial

                init_eq, init_fut = _partition_symbols(initial)
                if init_eq:
                    await stream.level_one_equity_subs(
                        init_eq, fields=_EQUITY_FIELDS
                    )
                if init_fut:
                    await stream.level_one_futures_subs(
                        init_fut, fields=_FUTURES_FIELDS
                    )

                while self._running:
                    await self._apply_stream_changes(stream)

                    try:
                        await asyncio.wait_for(
                            stream.handle_message(),
                            timeout=_HANDLE_MESSAGE_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Schwab stream: no message in %.0fs — reconnecting",
                            _HANDLE_MESSAGE_TIMEOUT,
                        )
                        break

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                exc_type = type(exc).__name__
                logger.warning(
                    "Schwab market stream error (%s: %s); reconnecting in %.0fs",
                    exc_type,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SEC)
            finally:
                if stream is not None:
                    try:
                        await stream.logout()
                    except Exception:
                        pass

    async def _apply_stream_changes(self, stream: StreamClient) -> None:
        """Drain pending add/remove sets and send the appropriate stream commands."""
        if not (self._pending_add or self._pending_remove):
            return

        async with self._lock:
            pending_add = set(self._pending_add)
            pending_remove = set(self._pending_remove)
            self._pending_add.clear()
            self._pending_remove.clear()

        if pending_remove:
            rm_eq, rm_fut = _partition_symbols(pending_remove)
            if rm_eq:
                await stream.level_one_equity_unsubs(rm_eq)
            if rm_fut:
                await stream.level_one_futures_unsubs(rm_fut)

        if pending_add:
            add_eq, add_fut = _partition_symbols(pending_add)
            # Use _add (not _subs) for live additions so existing subscriptions
            # are not replaced.
            if add_eq:
                await stream.level_one_equity_add(
                    add_eq, fields=_EQUITY_FIELDS
                )
            if add_fut:
                await stream.level_one_futures_add(
                    add_fut, fields=_FUTURES_FIELDS
                )

    # ------------------------------------------------------------------
    # Message handlers (called by StreamClient on each tick)
    # ------------------------------------------------------------------

    def _on_equity(self, msg: dict[str, Any]) -> None:
        """Level-one equity handler — called synchronously by StreamClient.

        Session-gated (computed once per message batch):
          • regular → the tick drives state.price / change_pct / sparkline and
            fires RTH price alerts — exactly the prior behavior.
          • pre / post / overnight / closed → state.price / change_pct / sparkline
            are FROZEN (the 4pm regular close outside RTH); the equity stream keeps
            delivering after-hours trades but they must NOT drag the regular price.
            The tick instead drives the extended fields (ext_price / ext_change_pct
            vs the frozen close, else prev_close / market_session) — mirroring the
            REST apply_extended poll. Bid/ask/volume still update (informative).

        Boundaries need no state migration: at 09:30 the gate flips to `regular`
        and the first regular tick overwrites the frozen price; at 16:00 the last
        regular tick simply stays as the frozen close while later post ticks route
        to the ext fields. Futures (_on_futures) are untouched — they trade ~23h on
        one live last.
        """
        session = market_session()
        is_regular = session == "regular"
        # StreamClient delivers a list of content dicts
        for item in msg.get("content", []):
            symbol = str(item.get("key", "")).upper()
            if not symbol:
                continue
            last = item.get(_EQ.LAST_PRICE.name)
            mark = item.get(_EQ.MARK.name)
            price_raw = last if last is not None else mark
            if price_raw is None:
                continue
            price_f = float(price_raw)

            bid_raw = item.get(_EQ.BID_PRICE.name)
            ask_raw = item.get(_EQ.ASK_PRICE.name)
            vol_raw = item.get(_EQ.TOTAL_VOLUME.name)
            chg_pct_raw = item.get(_EQ.NET_CHANGE_PERCENT.name)
            close_raw = item.get(_EQ.CLOSE_PRICE.name)

            state = self._quotes.get(symbol)
            if not state:
                state = QuoteState()
                self._quotes[symbol] = state

            ref = state.prev_close
            if ref > 0 and (
                price_f > ref * _MAX_TICK_RATIO or price_f * _MAX_TICK_RATIO < ref
            ):
                logger.warning(
                    "Dropping implausible %s tick %.4f (prior close %.2f)",
                    symbol,
                    price_f,
                    ref,
                )
                continue

            # Bid/ask/volume update in every session (informative, harmless).
            if bid_raw is not None:
                state.bid = float(bid_raw)
            if ask_raw is not None:
                state.ask = float(ask_raw)
            if vol_raw is not None:
                state.volume = float(vol_raw)

            if not is_regular:
                # Route after-hours trades to the extended fields; leave the
                # regular price frozen. ext_change_pct is measured vs the frozen
                # regular close (state.price) when set, else the prior daily close.
                # Cross-detection mirrors apply_extended (needs a prior ext value);
                # dedup vs the REST poll is structural — both write state.ext_price
                # from the SAME Schwab quote, so a repeated price yields no move and
                # only one path fires per distinct price.
                prev_ext = state.ext_price
                state.ext_price = price_f
                base = state.price if state.price else state.prev_close
                state.ext_change_pct = (
                    round((price_f - base) / base * 100, 2) if base else None
                )
                state.market_session = session
                self._schedule_post_tick(symbol, prev_ext, price_f, ext=True)
                continue

            state.price = price_f
            if close_raw is not None:
                state.prev_close = float(close_raw)
            if chg_pct_raw is not None:
                state.change_pct = round(float(chg_pct_raw), 2)
            elif state.prev_close:
                state.change_pct = round(
                    (price_f - state.prev_close) / state.prev_close * 100, 2
                )

            if state.sparkline:
                state.sparkline = [*state.sparkline[:-1], round(price_f, 4)]
            else:
                state.sparkline = [round(price_f, 4)]

            prev = self._prev_prices.get(symbol)
            self._prev_prices[symbol] = price_f

            # Schedule async follow-ups (broadcast + alerts) without blocking the
            # sync handler. schwab-py invokes us from inside its asyncio loop, so
            # get_running_loop() is the right call (get_event_loop() is deprecated
            # and can return a wrong loop in 3.12+).
            self._schedule_post_tick(symbol, prev, price_f)

    def _on_futures(self, msg: dict[str, Any]) -> None:
        """Level-one futures handler — called synchronously by StreamClient."""
        for item in msg.get("content", []):
            symbol = str(item.get("key", "")).upper()
            if not symbol:
                continue
            last = item.get(_FUT.LAST_PRICE.name)
            if last is None:
                continue
            price_f = float(last)

            bid_raw = item.get(_FUT.BID_PRICE.name)
            ask_raw = item.get(_FUT.ASK_PRICE.name)
            vol_raw = item.get(_FUT.TOTAL_VOLUME.name)
            chg_pct_raw = item.get(_FUT.FUTURE_CHANGE_PERCENT.name)
            close_raw = item.get(_FUT.CLOSE_PRICE.name)

            state = self._quotes.get(symbol)
            if not state:
                state = QuoteState()
                self._quotes[symbol] = state

            ref = state.prev_close
            if ref > 0 and (
                price_f > ref * _MAX_TICK_RATIO or price_f * _MAX_TICK_RATIO < ref
            ):
                logger.warning(
                    "Dropping implausible %s futures tick %.4f (prior close %.2f)",
                    symbol,
                    price_f,
                    ref,
                )
                continue

            state.price = price_f
            if bid_raw is not None:
                state.bid = float(bid_raw)
            if ask_raw is not None:
                state.ask = float(ask_raw)
            if vol_raw is not None:
                state.volume = float(vol_raw)
            if close_raw is not None:
                state.prev_close = float(close_raw)
            if chg_pct_raw is not None:
                state.change_pct = round(float(chg_pct_raw), 2)
            elif state.prev_close:
                state.change_pct = round(
                    (price_f - state.prev_close) / state.prev_close * 100, 2
                )

            if state.sparkline:
                state.sparkline = [*state.sparkline[:-1], round(price_f, 4)]
            else:
                state.sparkline = [round(price_f, 4)]

            prev = self._prev_prices.get(symbol)
            self._prev_prices[symbol] = price_f

            self._schedule_post_tick(symbol, prev, price_f)

    def _schedule_post_tick(
        self, symbol: str, prev: float | None, current: float, *, ext: bool = False
    ) -> None:
        """Fan a tick's broadcast+alert work off the sync stream handler.

        Schwab-py invokes our handlers from inside its asyncio loop, so the
        running loop is available. Failures are logged (not silently dropped) and
        a hard reference is kept until the task finishes — otherwise the GC can
        cancel the task before _broadcast_quote runs (the docs' "you must keep a
        strong reference" rule). ``ext=True`` routes to the extended-hours follow-up
        (fires ext alerts, no RTH gate); the default is the regular-session path.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("Schwab tick %s: no running loop — dropping", symbol)
            return
        coro = (
            self._ext_post_tick(symbol, prev, current)
            if ext
            else self._post_tick(symbol, prev, current)
        )
        task = loop.create_task(coro)
        self._inflight_ticks.add(task)
        task.add_done_callback(self._inflight_ticks.discard)

    async def _ext_post_tick(
        self, symbol: str, prev_ext: float | None, current: float
    ) -> None:
        """Broadcast the ext quote and fire price alerts on the extended move.

        Reached only outside RTH (the session gate in _on_equity routes here), so
        there's no is_market_open() guard — off-hours ext alerts are exactly what
        should fire, matching the REST apply_extended poll. Dedup against that poll
        is structural: both read/write state.ext_price, so a repeated price gives
        prev_ext == current and no fire.
        """
        await self._broadcast_quote(symbol)
        if prev_ext is None or prev_ext == current:
            return
        await self._check_price_alerts(symbol, prev_ext, current)

    async def _post_tick(
        self, symbol: str, prev: float | None, current: float
    ) -> None:
        """Broadcast the updated quote and fire price alerts."""
        await self._broadcast_quote(symbol)
        if prev is None or prev == current:
            return
        # Equity LAST_PRICE is the live price only during RTH — pre/post-market it
        # reverts to the frozen regular-session last, which flickers against the live
        # extended price and fires phantom alert crosses ("SPY cross 731.27 — now
        # 728.99"). Off-hours equity alerts come from the REST extended-quote poll
        # (apply_extended) on the true current price instead. Futures trade ~23h with
        # a single live last, so their stream alerts always apply.
        if symbol.startswith("/") or is_market_open():
            await self._check_price_alerts(symbol, prev, current)
