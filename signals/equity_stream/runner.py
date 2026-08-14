"""Wires the equity collector: wildcard stream → detectors → DB, plus the proxy.

Per-symbol state lives here (last consolidated price for the tick test, live
NBBO for direction). The flush loop mirrors api/main.py's
_equity_tape_flush_loop exactly — insert_event upserts on deterministic id,
then options_flow.convergence.process_events runs the confluence watch — so
moving detection out of process changes WHERE it runs, not what it produces.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import numpy as np

from brokers.market_stream import TRADE_EXCLUDE_CONDITIONS
from signals.abnormality.detector import AbnormalityDetector
from signals.equity_stream.constants import (
    FLUSH_INTERVAL_SECONDS,
    SPOT_FLUSH_INTERVAL_SECONDS,
    STATS_INTERVAL_SECONDS,
)
from signals.equity_stream.detectors import (
    Nbbo,
    SpreadStats,
    SweepAggregator,
    VelocityDetector,
    side_vs_nbbo,
)
from signals.equity_stream.proxy import LocalMarketProxy
from signals.equity_stream.stream import EquityStream, to_json_msg, ts_to_iso, ts_to_ms
from signals.equity_tape import TapeDetector
from trading.market_hours import _ET, _SESSION_OPEN

logger = logging.getLogger(__name__)


class EquityTapeRunner:
    def __init__(self, *, key: str, secret: str) -> None:
        self.stream = EquityStream(
            key=key,
            secret=secret,
            on_trade=self._on_trade,
            on_quote=self._on_quote,
            on_daily_bar=self._on_daily_bar,
        )
        self.proxy = LocalMarketProxy(
            on_daily_bar_symbols_change=self.stream.set_daily_bar_symbols
        )
        # Couple proxy availability to upstream health: while our Alpaca socket
        # is down, the hub must see a refused/dropped connection, not a silent
        # "connected" feed with no data.
        self.stream.on_connect = lambda: self.proxy.set_upstream_healthy(True)
        self.stream.on_disconnect = lambda: self.proxy.set_upstream_healthy(False)
        self.detector = TapeDetector()
        self.sweeps = SweepAggregator()
        self.velocity = VelocityDetector()
        self.abnormality = AbnormalityDetector()
        self.spreads = SpreadStats()
        self._nbbo: dict[str, Nbbo] = {}
        self._last_price: dict[str, float] = {}
        # Changed-since-last-flush consolidated prices → fusion_spot_cache
        # (symbol → (price, print UTC ISO ts)). Fed beside _last_price, so the
        # exclude-condition sanity the tick test relies on guards spots too.
        self._spot_dirty: dict[str, tuple[float, str]] = {}
        self._pending: list[dict[str, Any]] = []
        self._running = True
        self._persisted = 0
        self._spots_flushed = 0
        self._key = key
        self._secret = secret
        self._abn_reloaded_session = None

    def stop(self) -> None:
        self._running = False
        self.stream.stop()

    # -- hot path (sync, on the stream recv loop) ---------------------------------

    def _on_quote(self, m: dict[str, Any], now_ms: float) -> None:
        symbol = m.get("S")
        try:
            bid = float(m["bp"])
            ask = float(m["ap"])
        except (KeyError, TypeError, ValueError):
            return
        if not symbol:
            return
        ts_ms = ts_to_ms(m.get("t")) or now_ms
        self._nbbo[symbol] = (bid, ask, ts_ms)
        self.spreads.on_quote(symbol, bid, ask)
        if self.proxy.wants("quotes", symbol):
            self.proxy.publish("quotes", symbol, to_json_msg(m))

    def _on_trade(self, m: dict[str, Any], now_ms: float) -> None:
        symbol = m.get("S")
        price = m.get("p")
        if not symbol or price is None:
            return
        price_f = float(price)
        try:
            size = int(m.get("s") or 0)
        except (TypeError, ValueError):
            size = 0
        conditions = m.get("c") or []
        ts_ms = ts_to_ms(m.get("t")) or now_ms
        nbbo = self._nbbo.get(symbol)

        # Large-print tap BEFORE the exclude-condition drop, mirroring the hub:
        # average-price/derivative crosses are often the genuine institutional
        # blocks. Timestamp goes in as ISO for the event id/date fields.
        if size and price_f > 0:
            self.detector.on_print(
                symbol=symbol,
                price=price_f,
                size=size,
                exchange=m.get("x"),
                conditions=conditions,
                ts=ts_to_iso(m.get("t")),
                trade_id=m.get("i"),
                prev_price=self._last_price.get(symbol),
            )
            iso = ts_to_iso(m.get("t"))
            if iso:
                try:
                    ts_et = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_ET)
                    self.abnormality.on_print(symbol, price_f, size, ts_et)
                except (TypeError, ValueError):
                    pass

            if "F" in conditions:
                closed = self.sweeps.on_iso_print(
                    symbol=symbol,
                    price=price_f,
                    size=size,
                    exchange=str(m.get("x") or "?"),
                    ts_ms=ts_ms,
                    nbbo=nbbo,
                )
                if closed is not None:
                    self._pending.append(closed)

        excluded = any(c in TRADE_EXCLUDE_CONDITIONS for c in conditions)
        if not excluded and size and price_f > 0:
            direction, _basis = side_vs_nbbo(price_f, nbbo, ts_ms)
            signed = price_f * size
            if direction == "sell_side":
                signed = -signed
            elif direction != "buy_side":
                signed = 0.0
            spike = self.velocity.on_print(
                symbol=symbol,
                price=price_f,
                size=size,
                ts_ms=ts_ms,
                signed_notional=signed,
            )
            if spike is not None:
                self._pending.append(spike)
            self._last_price[symbol] = price_f
            iso = ts_to_iso(m.get("t"))
            if iso:
                self._spot_dirty[symbol] = (price_f, iso)

        if self.proxy.wants("trades", symbol):
            self.proxy.publish("trades", symbol, to_json_msg(m))

    def _on_daily_bar(self, m: dict[str, Any]) -> None:
        symbol = m.get("S")
        if symbol and self.proxy.wants("dailyBars", symbol):
            self.proxy.publish("dailyBars", symbol, to_json_msg(m))

    # -- loops ---------------------------------------------------------------------

    async def flush_loop(self) -> None:
        """Persist queued events + run the confluence watch (same shape as the
        API's _equity_tape_flush_loop, which stands down when we own the tape)."""
        from db.equity_tape import insert_event
        from signals.options_flow.convergence import process_events

        while self._running:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            try:
                events: list[dict[str, Any]] = []
                while self.detector.pending:
                    events.append(self.detector.pending.popleft())
                now_ms = time.time() * 1000.0
                events.extend(self.sweeps.flush(now_ms))
                events.extend(self.velocity.flush(now_ms))
                if self._pending:
                    events.extend(self._pending)
                    self._pending = []
                for ev in events:
                    await asyncio.to_thread(insert_event, ev)
                self._persisted += len(events)
                abn_events, abn_state = self.abnormality.take_pending()
                if abn_events or abn_state:
                    await asyncio.to_thread(self._persist_abnormality, abn_events, abn_state)

                if events:
                    try:
                        from brokers.factory import get_broker

                        broker = get_broker("alpaca")
                    except Exception:
                        broker = None
                    if broker is not None:
                        now_et = datetime.now(_ET)
                        open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
                        now_min = now_et.hour * 60 + now_et.minute
                        conv = await process_events(
                            broker,
                            events,
                            today_et=now_et.date().isoformat(),
                            minutes_since_open=now_min - open_min,
                        )
                        if conv.get("convergence") or conv.get("pushes"):
                            logger.info(
                                "equity tape: events=%s convergence=%s pushes=%s",
                                len(events),
                                conv.get("convergence", 0),
                                conv.get("pushes", 0),
                            )
            except Exception:
                logger.exception("equity tape: flush failed")

    def _take_spot_dirty(self) -> dict[str, tuple[float, str]]:
        """Swap out the dirty map. MUST run on the event loop thread — the
        same pattern as flush_loop's `self._pending = []` swap. Swapping on a
        worker thread races _on_trade's inserts and a close-burst batch dies
        to 'dictionary changed size during iteration' (review S1)."""
        dirty, self._spot_dirty = self._spot_dirty, {}
        return dirty

    def _write_spots(
        self, dirty: dict[str, tuple[float, str]], *, db_path: Any = None
    ) -> int:
        """Batch-upsert a swapped-out dirty map (sync; runs via to_thread).
        A failed write drops the batch on the floor deliberately: the next
        prints re-dirty the active names, and freshness beats completeness
        for a spot cache. ``db_path`` is for tests (review S8) — production
        writes the shared default DB like every other collector write."""
        from db.fusion import put_spots

        if not dirty:
            return 0
        rows = [(s, str(p), ts) for s, (p, ts) in dirty.items()]
        n = put_spots(rows, **({} if db_path is None else {"db_path": db_path}))
        self._spots_flushed += n
        return n

    async def spot_flush_loop(self) -> None:
        """Whole-market price source for the fusion intake (fusion_spot_cache):
        the wildcard tape sees every US print anyway — this just persists the
        consolidated last price per active symbol on a 30s cadence so the
        backend can price options-flow underlyings beyond the watchlist."""
        while self._running:
            await asyncio.sleep(SPOT_FLUSH_INTERVAL_SECONDS)
            dirty = self._take_spot_dirty()  # loop thread — never in to_thread
            if not dirty:
                continue
            try:
                await asyncio.to_thread(self._write_spots, dirty)
            except Exception:
                logger.exception("equity tape: spot flush failed")

    async def stats_loop(self) -> None:
        last_msgs = 0
        while self._running:
            await asyncio.sleep(STATS_INTERVAL_SECONDS)
            try:
                total = sum(self.stream.msgs.values())
                rate = (total - last_msgs) / STATS_INTERVAL_SECONDS
                last_msgs = total
                p50, p95 = self.stream.lag_percentiles()
                wideners = ", ".join(
                    f"{s}:{r:.0f}x@{bps:.0f}bps"
                    for s, r, bps in self.spreads.top_wideners(5)
                )
                logger.info(
                    "equity stream stats: msgs=%d (%.0f/s) t=%d q=%d "
                    "reconnects=%d vendor_errors=%d lag_p50_ms=%.0f lag_p95_ms=%.0f "
                    "| events persisted=%d sweeps=%d velocity=%d spots=%d "
                    "| proxy clients=%d subs t/q/db=%d/%d/%d drops=%d "
                    "| wideners: %s",
                    total,
                    rate,
                    self.stream.msgs.get("t", 0),
                    self.stream.msgs.get("q", 0),
                    self.stream.reconnects,
                    self.stream.vendor_errors,
                    p50,
                    p95,
                    self._persisted,
                    self.sweeps.emitted,
                    self.velocity.emitted,
                    self._spots_flushed,
                    self.proxy.client_count,
                    self.proxy.union_size("trades"),
                    self.proxy.union_size("quotes"),
                    self.proxy.union_size("dailyBars"),
                    self.proxy.total_drops,
                    wideners or "-",
                )
            except Exception:
                logger.exception("equity stream: stats failed")

    def _persist_abnormality(
        self, events: list[dict[str, Any]], state: list[dict[str, Any]]
    ) -> None:
        from db.abnormality import insert_events, upsert_state

        if events:
            insert_events(events)
        if state:
            upsert_state(state)

    def _load_baselines(self) -> None:
        from signals.abnormality.baselines import load_baselines

        today = datetime.now(_ET).date()
        try:
            bl = load_baselines()
        except Exception:
            logger.exception("abnormality: failed to load baselines")
            bl = None
        self.abnormality.set_baselines(bl, today)
        self._abn_reloaded_session = today

    def _prime_today(self) -> None:
        """Bounded bulk fetch of today's 5-min bars so mid-session restart isn't blind."""
        from signals.abnormality.constants import N_BUCKETS
        from signals.abnormality.store import timestamps_to_buckets
        from tools.equity_bars_5min_backfill import fetch_session

        bl = self.abnormality._baselines
        if bl is None:
            return
        today = datetime.now(_ET).date()
        headers = {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }
        symbols = [str(s) for s in bl.symbols]
        try:
            per_sym, _n = fetch_session(today, symbols, headers)
        except Exception:
            logger.exception("abnormality: prime fetch failed")
            return
        n = len(symbols)
        idx = {s: i for i, s in enumerate(symbols)}
        high = np.full((n, N_BUCKETS), np.nan)
        low = np.full((n, N_BUCKETS), np.nan)
        close = np.full((n, N_BUCKETS), np.nan)
        vol = np.full((n, N_BUCKETS), np.nan)
        for sym, parts in per_sym.items():
            i = idx.get(sym)
            if i is None:
                continue
            t = np.concatenate([p[0] for p in parts])
            h = np.concatenate([p[2] for p in parts])
            lo = np.concatenate([p[3] for p in parts])
            c = np.concatenate([p[4] for p in parts])
            v = np.concatenate([p[5] for p in parts]).astype(np.float64)
            buckets = timestamps_to_buckets(t, today)
            ok = (buckets >= 0) & (buckets < N_BUCKETS)
            high[i, buckets[ok]] = h[ok]
            low[i, buckets[ok]] = lo[ok]
            close[i, buckets[ok]] = c[ok]
            vol[i, buckets[ok]] = v[ok]
        self.abnormality.prime_from_grid(np.array(symbols, dtype="U8"), high, low, close, vol)
        logger.info("abnormality: primed %d symbols for %s", n, today)

    async def abnormality_loop(self) -> None:
        """5-min boundary ticks + pre-open baseline reload. Never on the recv loop."""
        await asyncio.to_thread(self._load_baselines)
        now = datetime.now(_ET)
        open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
        now_min = now.hour * 60 + now.minute
        # Prime only during RTH — a post-close deploy must not pull the whole
        # universe just to fold a session that's already over.
        if open_min <= now_min < 16 * 60:
            await asyncio.to_thread(self._prime_today)
        while self._running:
            await asyncio.sleep(1)
            try:
                now = datetime.now(_ET)
                today = now.date()
                # Reload once pre-open (09:20–09:30) so the new artifact is in
                # before the first boundary. Calendar-gated: weekends skip.
                if (
                    now.hour == 9
                    and 20 <= now.minute < 30
                    and self._abn_reloaded_session != today
                ):
                    await asyncio.to_thread(self._load_baselines)
                    self.abnormality.reset_session(today)
                await asyncio.to_thread(self.abnormality.maybe_boundary, now)
            except Exception:
                logger.exception("abnormality: boundary loop failed")


