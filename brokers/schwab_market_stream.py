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
import time
from typing import Any

from schwab.streaming import StreamClient

from brokers.bar_builder import Bar
from brokers.errors import BrokerError
from brokers.market_clock import market_session
from brokers.market_stream import (
    RECONNECT_BASE_SEC,
    RECONNECT_MAX_SEC,
    TICK_ABS_CEILING,
    TICK_BAND_PCT,
    TICK_CONFIRM_TOL,
    BaseMarketHub,
    QuoteState,
    _daily_prev_close,
)
from config.settings import broker_is_configured
from trading.market_hours import is_market_open

logger = logging.getLogger(__name__)

_HANDLE_MESSAGE_TIMEOUT = 30.0  # seconds before assuming the stream dropped
_ACCOUNT_ACTIVITY_SUB_TIMEOUT = 10.0

# Suspect-print guard (band + one-tick confirmation; constants shared with the REST
# apply_extended guard via brokers.market_stream). A tick outside ±TICK_BAND_PCT of the
# reference — the LAST ADMITTED price, seeded from the prior close before any tick has
# been admitted — is "suspect" (a bad print / crossed quote candidate), not a
# self-evidently real intraday move. Suspect ticks are HELD for one tick and only
# admitted if the immediately preceding raw tick corroborated the same level within
# TICK_CONFIRM_TOL — so a lone spike between two normal prints is dropped (never
# reaching state.price / sparkline / alerts / the RiskEngine live quote / charts),
# while a legitimate fast move / gap / halt-reopen confirms tick-over-tick, is admitted
# after ~one held tick, and RE-CENTERS the band on the new level so subsequent normal
# swings flow freely (referencing prev_close instead would freeze a gapped runner all
# day). The old single-tick 3× guard never fired in practice: real bad prints (~2× the
# true price) sailed under it.
_HELD_LOG_INTERVAL_SEC = 60.0  # rate-limit held-tick WARNINGs per symbol
_FROZEN_STREAK = 20  # consecutive held ticks that escalate to ERROR ("price frozen")

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
_CHART = StreamClient.ChartFuturesFields
_ACCT = StreamClient.AccountActivityFields


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


async def _build_stream_client() -> tuple[StreamClient, dict[str, str]]:
    """Create a stream client plus account-number → hash routing map."""
    from brokers.schwab_auth import create_client

    client = await asyncio.to_thread(create_client, interactive=False)
    account_hashes: dict[str, str] = {}
    try:
        response = await asyncio.to_thread(client.get_account_numbers)
        response.raise_for_status()
        for row in response.json() or []:
            number = str(row.get("accountNumber") or "").strip()
            account_hash = str(row.get("hashValue") or "").strip()
            if number and account_hash:
                account_hashes[number] = account_hash
    except Exception as exc:  # noqa: BLE001 — activity can fall back to all accounts
        logger.warning(
            "Schwab account-activity routing map unavailable (%s); "
            "activity will refresh all mapped accounts",
            exc,
        )
    # account_id is vestigial in schwab-py 1.5.1 — omit it
    return StreamClient(client), account_hashes


class SchwabMarketStreamHub(BaseMarketHub):
    """Market-data hub backed by Schwab level-one streaming quotes."""

    def __init__(self) -> None:
        super().__init__()
        # Hard references to in-flight per-tick tasks so the GC can't cancel
        # _broadcast_quote / _check_price_alerts mid-run (the cause of
        # "ticks reach the handler but seem to disappear" reports).
        self._inflight_ticks: set[asyncio.Task] = set()
        # Last raw price seen per symbol (regular + ext share one slot) — the
        # corroboration reference for the suspect-tick confirmation guard. Updated
        # on EVERY tick (in- or out-of-band, held or admitted) so consecutive
        # ticks can confirm each other; see _is_held_suspect_tick.
        self._pending_tick: dict[str, float] = {}
        # Last ADMITTED price per symbol (regular + ext share one slot — same
        # instrument, and the guard runs before the session split) — the band
        # reference. Seeded from prev_close when unset, re-centered on every admit
        # so a confirmed gap doesn't freeze subsequent normal swings.
        self._last_admitted: dict[str, float] = {}
        # Held-tick log bookkeeping (per symbol): consecutive-held streak (resets on
        # admit), holds since the last log line, and monotonic time of that log.
        self._held_streak: dict[str, int] = {}
        self._held_since_log: dict[str, int] = {}
        self._held_log_ts: dict[str, float] = {}
        # Account Activity names the numeric Schwab account; ledger sync scopes
        # by its opaque hash. Built authoritatively from get_account_numbers on
        # every stream connection — never infer this through labels/last-four.
        self._activity_account_hashes: dict[str, str] = {}

    def _is_held_suspect_tick(
        self, symbol: str, price_f: float, prev_close: float, *, label: str
    ) -> bool:
        """Return True if *price_f* is a suspect print that must be HELD this cycle.

        A tick is "suspect" when outside ±TICK_BAND_PCT of the LAST ADMITTED price
        (seeded from *prev_close* before any admit). A suspect tick is held (dropped,
        no state/broadcast/alert write) UNLESS the immediately preceding raw tick
        corroborated the same level (within TICK_CONFIRM_TOL). No reference at all
        (no admit yet AND prev_close missing/≤0) skips the band check entirely —
        existing admit-everything behavior. The pending reference is ALWAYS updated
        (suspect or not) so consecutive out-of-band ticks corroborate each other.
        Every admit re-centers the band, which makes the guard self-healing: even if
        two same-level bogus prints phantom-admit, the next two real ticks are
        out-of-band vs the phantom, corroborate each other, and re-center the
        reference on reality within ~2 ticks.

        Held-tick logging: WARNING at most once per symbol per _HELD_LOG_INTERVAL_SEC
        (with the held-count since the last line), escalating to ERROR when a symbol
        reaches _FROZEN_STREAK consecutive holds — the real "price frozen" alarm.
        """
        pending = self._pending_tick.get(symbol)
        self._pending_tick[symbol] = price_f
        ref = self._last_admitted.get(symbol)
        if ref is None:
            ref = prev_close
        # Absolute backstop vs prev_close: corroboration can never admit a print
        # at 10x/0.1x yesterday's close — stops a corrupt feed from WALKING the
        # rolling reference far from reality in admitted ≤5% steps.
        absurd = prev_close > 0 and (
            price_f > prev_close * TICK_ABS_CEILING
            or price_f * TICK_ABS_CEILING < prev_close
        )
        admit = not absurd and (
            ref <= 0  # no baseline yet → admit everything (existing behavior)
            or ref * (1 - TICK_BAND_PCT) <= price_f <= ref * (1 + TICK_BAND_PCT)
            or (
                # corroborated by the preceding tick → legitimate fast move / gap
                pending is not None
                and abs(price_f - pending) <= price_f * TICK_CONFIRM_TOL
            )
        )
        if admit:
            self._last_admitted[symbol] = price_f
            self._held_streak[symbol] = 0
            return False

        streak = self._held_streak.get(symbol, 0) + 1
        self._held_streak[symbol] = streak
        self._held_since_log[symbol] = self._held_since_log.get(symbol, 0) + 1
        now = time.monotonic()
        last_log = self._held_log_ts.get(symbol)
        if streak == _FROZEN_STREAK:
            logger.error(
                "%s %s price FROZEN: %d consecutive suspect prints held "
                "(latest %.4f, ref %.2f, pending %s)",
                symbol,
                label,
                streak,
                price_f,
                ref,
                f"{pending:.4f}" if pending is not None else "none",
            )
            self._held_log_ts[symbol] = now
            self._held_since_log[symbol] = 0
        elif last_log is None or now - last_log >= _HELD_LOG_INTERVAL_SEC:
            logger.warning(
                "Holding suspect %s %s %.4f (ref %.2f, pending %s, "
                "%d held since last log)",
                symbol,
                label,
                price_f,
                ref,
                f"{pending:.4f}" if pending is not None else "none",
                self._held_since_log[symbol],
            )
            self._held_log_ts[symbol] = now
            self._held_since_log[symbol] = 0
        return True

    def _live_flag(self) -> bool:
        return broker_is_configured("schwab")

    # ------------------------------------------------------------------
    # Baselines: seed QuoteState from recent daily bars so the UI has
    # non-zero prices before the first stream tick arrives.
    # ------------------------------------------------------------------

    async def _ensure_baselines(self, symbols: set[str], force: bool = False) -> bool:
        eq, fut = _partition_symbols(symbols)
        missing_eq = [s for s in eq if force or s not in self._quotes]
        # Futures have no daily-close baseline — never force-reset a live one.
        missing_fut = [s for s in fut if s not in self._quotes]

        # Futures get an empty QuoteState; the first stream tick populates them.
        for sym in missing_fut:
            self._quotes[sym] = QuoteState()

        if not missing_eq:
            return True

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

        if not bars_by_symbol:
            # Both brokers failed — leave existing states alone so a forced
            # roll can retry, and report the failure.
            for sym in missing_eq:
                if sym not in self._quotes:
                    self._quotes[sym] = QuoteState()
            return False

        for sym in missing_eq:
            bars = bars_by_symbol.get(sym, [])
            old = self._quotes.get(sym)
            if not bars:
                if old is None:
                    self._quotes[sym] = QuoteState()
                continue
            closes = [b.close for b in bars]
            price = closes[-1]
            prev = _daily_prev_close(bars)
            change_pct = ((price - prev) / prev * 100) if prev else 0.0
            state = QuoteState(
                price=price,
                change_pct=round(change_pct, 2),
                sparkline=[round(c, 4) for c in closes[-7:]],
                prev_close=prev,
            )
            if old is not None:
                state.ext_price = old.ext_price
                state.ext_change_pct = old.ext_change_pct
                state.market_session = old.market_session
            self._quotes[sym] = state
            self._prev_prices[sym] = price
        return True

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
                stream, self._activity_account_hashes = await _build_stream_client()

                # Register handlers BEFORE login
                stream.add_level_one_equity_handler(self._on_equity)
                stream.add_level_one_futures_handler(self._on_futures)
                stream.add_chart_futures_handler(self._on_chart_futures)
                stream.add_account_activity_handler(self._on_account_activity)

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
                    await stream.chart_futures_subs(init_fut)
                await self._subscribe_account_activity(stream)

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
                await stream.chart_futures_unsubs(rm_fut)

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
                await stream.chart_futures_add(add_fut)

    async def _subscribe_account_activity(self, stream: StreamClient) -> bool:
        """Subscribe without making account activity a market-data dependency.

        A failure here must not reconnect-loop or take quotes offline. Position
        observation and the hourly transaction sweep remain the fallback.
        """
        try:
            await asyncio.wait_for(
                stream.account_activity_sub(),
                timeout=_ACCOUNT_ACTIVITY_SUB_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 — optional acceleration lane
            logger.warning(
                "Schwab account activity unavailable (%s); "
                "position/hourly ledger refresh remains active",
                exc,
            )
            return False
        logger.info("Schwab account activity subscribed")
        return True

    # ------------------------------------------------------------------
    # Message handlers (called by StreamClient on each tick)
    # ------------------------------------------------------------------

    def _on_account_activity(self, msg: dict[str, Any]) -> None:
        """Queue a transaction-ledger sweep for any real account activity.

        Account Activity covers fills that never survive until the next
        positions snapshot (the futures day-trading failure mode). Subscription
        acknowledgements are response frames, not handler content; still require
        at least one content item so malformed/empty frames cannot trigger work.
        The downstream request is non-blocking and independently bounded.
        """
        content = msg.get("content") or []
        if not content:
            return
        try:
            from trading.position_trigger import observe_account_activity

            hashes: set[str] = set()
            complete_mapping = True
            for item in content:
                account_number = str(item.get(_ACCT.ACCOUNT.name) or "").strip()
                account_hash = self._activity_account_hashes.get(account_number)
                if not account_hash:
                    complete_mapping = False
                    break
                hashes.add(account_hash)
            observe_account_activity(hashes if complete_mapping and hashes else None)
        except Exception:  # noqa: BLE001 — never break Schwab market streaming
            logger.exception("Schwab account-activity trigger failed")

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

            # Suspect-print guard (regular AND ext share _pending_tick per symbol —
            # a single guard here gates both the state.price write below and the ext
            # write in the non-regular branch). A held suspect tick writes nothing.
            if self._is_held_suspect_tick(
                symbol, price_f, state.prev_close, label="tick"
            ):
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
                # regular price frozen. ext_change_pct is measured vs the day
                # reference (prev_close — yesterday's regular close, ToS-style)
                # when seeded, else the frozen regular close.
                # Cross-detection mirrors apply_extended (needs a prior ext value);
                # dedup vs the REST poll is structural — both write state.ext_price
                # from the SAME Schwab quote, so a repeated price yields no move and
                # only one path fires per distinct price.
                prev_ext = state.ext_price
                state.ext_price = price_f
                base = state.prev_close if state.prev_close else state.price
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
            if symbol.startswith("$"):
                # Cash indices ($SPX) have no SIP trade stream: like futures, the
                # level-one last is the print that moves the live candle.
                completed = self._fold_print(symbol, price_f, 0.0, None)
                if completed is not None:
                    self._schedule_task(self._broadcast_bar(symbol, completed))

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
            vol_raw = item.get(_FUT.TOTAL_VOLUME.name)
            if last is None:
                # Level-one sends changed fields only: a trade at the unchanged
                # last moves TOTAL_VOLUME alone. Fold it as a print at that last
                # so a new minute opens where it really traded (measured
                # 2026-10-27: 11:03 opened 2 ticks late without this).
                state = self._quotes.get(symbol)
                prev = self._prev_prices.get(symbol)
                if state is not None and prev is not None and vol_raw is not None:
                    if float(vol_raw) > state.volume:
                        completed = self._fold_print(symbol, prev, 0.0, None)
                        if completed is not None:
                            self._schedule_task(self._broadcast_bar(symbol, completed))
                    state.volume = float(vol_raw)
                continue
            price_f = float(last)

            bid_raw = item.get(_FUT.BID_PRICE.name)
            ask_raw = item.get(_FUT.ASK_PRICE.name)
            chg_pct_raw = item.get(_FUT.FUTURE_CHANGE_PERCENT.name)
            close_raw = item.get(_FUT.CLOSE_PRICE.name)

            state = self._quotes.get(symbol)
            if not state:
                state = QuoteState()
                self._quotes[symbol] = state

            if self._is_held_suspect_tick(
                symbol, price_f, state.prev_close, label="futures tick"
            ):
                continue

            state.price = price_f
            # Futures have no SIP trade stream here — the level-one last (a ~1Hz
            # conflated snapshot) is the print for the FORMING minute.
            # Receipt-time bucketed, sizeless; Schwab's CHART_FUTURES candle
            # replaces it ~2-3s after the minute closes (_on_chart_futures).
            # Sync handler: fold now, fan the completed-minute frame off.
            completed = self._fold_print(symbol, price_f, 0.0, None)
            if completed is not None:
                self._schedule_task(self._broadcast_bar(symbol, completed))
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

    def _on_chart_futures(self, msg: dict[str, Any]) -> None:
        """CHART_FUTURES handler: Schwab's own finished minute (the bar
        thinkorswim draws), sent once ~2-3s after each minute closes — never
        mid-minute (measured 2026-10-27). Seal it over our level-one copy."""
        for item in msg.get("content", []):
            symbol = str(item.get("key", "")).upper()
            t = item.get(_CHART.CHART_TIME_MILLIS.name)
            o = item.get(_CHART.OPEN_PRICE.name)
            h = item.get(_CHART.HIGH_PRICE.name)
            lo = item.get(_CHART.LOW_PRICE.name)
            c = item.get(_CHART.CLOSE_PRICE.name)
            if not symbol or None in (t, o, h, lo, c):
                continue
            bar = Bar(
                int(t),
                float(o),
                float(h),
                float(lo),
                float(c),
                float(item.get(_CHART.VOLUME.name) or 0.0),
            )
            self._schedule_task(self._ingest_vendor_bar(symbol, bar))

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

    def _schedule_task(self, coro: Any) -> None:
        """Run a coroutine off the sync handler with the same strong-ref
        discipline as _schedule_post_tick."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
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
