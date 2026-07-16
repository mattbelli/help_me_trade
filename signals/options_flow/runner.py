"""Host-agnostic options-flow intraday pipeline: poll + OPRA stream + sweep consumer.

Extracted verbatim from api/main.py (2026-07-14) so the pipeline can run in
either host:

- inside the API process (legacy default — api/main.py wires OptionsFlowRunner
  into its job scheduler and lifespan), or
- as the standalone collector process (tools/flow_collector.py, run by the
  hmt-flow-collector systemd unit) so a stalled API event loop can't blind the
  stream and a hot open can't starve the trading loops.

Exactly ONE host may run the pipeline at a time: Alpaca allows a single OPRA
websocket per account, so a second host 406-fights the first into reconnect
backoff. The OPTIONS_FLOW_EXTERNAL_COLLECTOR env flag (read by both hosts from
the shared .env) is the interlock that flips ownership atomically.

The three legs share only in-process state owned by this module's host:
`signals.options_flow.poll._default_state` (subscription set + contract meta +
OI, filled by intraday_once) and the stream's in-memory print deque / quote
rings (drained by sweep_loop). Cross-process, everything travels through
data/trading.db (WAL, busy_timeout — see db/base.connect).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime

from brokers.factory import get_broker
from brokers.market_clock import market_session as _market_session
from config.settings import alpaca_config, broker_is_configured
from signals.options_flow.constants import PREOPEN_WARM_MINUTES
from trading.market_hours import _ET, _SESSION_OPEN

logger = logging.getLogger(__name__)


def _in_preopen_warm(now: datetime | None = None) -> bool:
    """Within PREOPEN_WARM_MINUTES of today's open (time-of-day only).

    The OI seed needs only prior-session data, so the poll can build + subscribe
    the active set before the bell instead of ~50s after it (measured 07-15: the
    session gate held the seed hostage through the open burst). Callers must ALSO
    check ``market_session() == "pre"`` — that delegates holiday/weekend logic to
    the market clock instead of duplicating it here.
    """
    now_et = (now or datetime.now(tz=_ET)).astimezone(_ET)
    open_dt = now_et.replace(
        hour=_SESSION_OPEN[0], minute=_SESSION_OPEN[1], second=0, microsecond=0
    )
    delta_s = (open_dt - now_et).total_seconds()
    return 0 < delta_s <= PREOPEN_WARM_MINUTES * 60


def _flow_session_active() -> bool:
    """Regular hours, or the pre-open warm window."""
    session = _market_session()
    if session == "regular":
        return True
    return session == "pre" and _in_preopen_warm()


def build_sweep_detail(
    snapshot: dict | None,
    underlying_price: float | None,
) -> dict[str, float] | None:
    """Pure helper: Alpaca option snapshot + underlying price → sweep detail dict."""
    detail: dict[str, float] = {}

    if underlying_price is not None:
        detail["underlying_price"] = round(float(underlying_price), 2)

    if isinstance(snapshot, dict):
        iv = snapshot.get("impliedVolatility")
        if iv is not None:
            try:
                detail["iv"] = round(float(iv), 4)
            except (TypeError, ValueError):
                pass

        greeks = snapshot.get("greeks")
        if isinstance(greeks, dict):
            for key in ("delta", "gamma", "theta", "vega", "rho"):
                val = greeks.get(key)
                if val is not None:
                    try:
                        detail[key] = round(float(val), 4)
                    except (TypeError, ValueError):
                        pass

        oi = snapshot.get("openInterest")
        if oi is not None:
            try:
                detail["oi"] = float(int(oi))
            except (TypeError, ValueError):
                pass

        quote = snapshot.get("latestQuote")
        if isinstance(quote, dict):
            for src, dst in (("bp", "bid"), ("ap", "ask")):
                val = quote.get(src)
                if val is not None:
                    try:
                        detail[dst] = round(float(val), 2)
                    except (TypeError, ValueError):
                        pass

    return detail or None


async def enrich_sweep_events(events: list[dict], broker: object) -> None:
    """Fail-soft sweep detail enrichment (underlying, greeks, IV, OI, quote)."""

    def _enrich() -> None:
        underlyings = {
            str(ev.get("underlying") or "").strip().upper() for ev in events
        }
        underlyings.discard("")
        und_prices: dict[str, float | None] = {}
        for sym in underlyings:
            try:
                snap = broker.get_stock_snapshot_fields(sym)
                und_prices[sym] = (
                    float(snap["last_price"]) if snap and snap.get("last_price") is not None else None
                )
            except Exception:
                logger.debug(
                    "options_flow sweeps: underlying snapshot failed for %s",
                    sym,
                    exc_info=True,
                )
                und_prices[sym] = None

        occs = [
            str(ev.get("occ_symbol") or "").strip().upper()
            for ev in events
            if str(ev.get("occ_symbol") or "").strip()
        ]
        snapshots: dict[str, dict] = {}
        if occs:
            try:
                snapshots = broker.get_option_snapshots(occs) or {}
            except Exception:
                logger.debug(
                    "options_flow sweeps: option snapshots failed",
                    exc_info=True,
                )

        # Snapshots don't carry openInterest (live-verified 2026-07-09); the
        # poll's daily OI refresh does — use it as the OI source.
        from signals.options_flow.poll import _default_state as _poll_state

        for ev in events:
            try:
                und = str(ev.get("underlying") or "").strip().upper()
                occ = str(ev.get("occ_symbol") or "").strip().upper()
                detail = build_sweep_detail(
                    snapshots.get(occ),
                    und_prices.get(und),
                )
                oi = _poll_state.oi.get(occ)
                if oi and "oi" not in (detail or {}):
                    detail = detail or {}
                    detail["oi"] = float(int(oi))
                ev["detail"] = detail or None
            except Exception:
                logger.debug(
                    "options_flow sweeps: detail build failed for %s",
                    ev.get("id"),
                    exc_info=True,
                )
                ev["detail"] = None

    try:
        await asyncio.to_thread(_enrich)
    except Exception:
        logger.debug("options_flow sweeps: enrichment failed", exc_info=True)
        for ev in events:
            ev.setdefault("detail", None)


class OptionsFlowRunner:
    """Owns the three pipeline legs and the stream handle they share.

    The host decides WHETHER each leg runs (env flags, scheduler); the runner
    only knows HOW. `watchlist_symbols` is injected because the two hosts
    source it differently (API: in-process settings cache; collector:
    mtime-refreshed re-read so watchlist edits made in the API are picked up
    without a restart).
    """

    def __init__(self, *, watchlist_symbols: Callable[[], list[str]]) -> None:
        self._watchlist_symbols = watchlist_symbols
        self.stream = None  # OptionsFlowStream | None — set while stream_run is live
        self._intraday_warned = False

    # --- stream callbacks (wired into OptionsFlowStream by stream_run) ---

    def meta_for(self, occ: str) -> dict | None:
        from signals.options_flow.poll import _default_state, _meta_from_occ

        sym = occ.strip().upper()
        meta = _default_state.active.get(sym)
        if meta is not None:
            return meta
        today_et = datetime.now(_ET).date().isoformat()
        return _meta_from_occ(sym, "", today_et)

    def subscription_set(self) -> list[str]:
        from signals.options_flow.poll import active_subscription_symbols

        if not _flow_session_active():
            return []
        return active_subscription_symbols()

    def anchor_quotes(self) -> list[str]:
        from signals.options_flow.poll import anchor_quote_symbols

        if not _flow_session_active():
            return []
        return anchor_quote_symbols()

    def on_prints(self, batch: list[dict]) -> None:
        stream = self.stream
        if stream is None:
            return
        # deque(maxlen) drops oldest SILENTLY on overflow — count it (audit F7a).
        # Safe margin today (~530 prints/min peak vs 5,000 cap, 1s drain), but the
        # gauge lane raises open-burst print rates on exactly the minutes we care
        # about; a silent drop here would truncate the gauge invisibly.
        maxlen = stream.collected_prints.maxlen
        overflow = (
            len(stream.collected_prints) + len(batch) - maxlen
            if maxlen is not None
            else 0
        )
        if overflow > 0:
            stream.dropped_prints += overflow
            logger.warning(
                "options_flow stream: collected_prints overflow — dropped %s oldest "
                "(consumer lagging)",
                overflow,
            )
        stream.collected_prints.extend(batch)

    # --- pipeline legs ---

    async def intraday_once(self) -> None:
        """Regular-hours intraday options-flow poll (+ pre-open warm); fail-soft."""
        try:
            if not _flow_session_active():
                return

            try:
                broker = get_broker("alpaca")
            except Exception as exc:
                logger.warning(
                    "options_flow intraday: Alpaca broker unavailable: %s", exc
                )
                return

            required = (
                "get_option_trades",
                "get_option_latest_quotes",
                "get_option_trade_conditions",
                "get_option_chain_snapshot",
                "get_option_contracts",
                "get_option_bars",
            )
            if not all(hasattr(broker, name) for name in required):
                if not self._intraday_warned:
                    logger.warning(
                        "options_flow intraday: broker missing options data methods "
                        "(options data requires Alpaca); skipping"
                    )
                    self._intraday_warned = True
                return

            from signals.options_flow.convergence import process_events
            from signals.options_flow.poll import run_poll_once

            symbols = self._watchlist_symbols()
            now_et = datetime.now(_ET)
            today_et = now_et.date().isoformat()
            now_ms = int(now_et.timestamp() * 1000)
            summary = await asyncio.to_thread(
                run_poll_once,
                broker,
                symbols=symbols,
                today_et=today_et,
                now_ms=now_ms,
            )
            events = summary.pop("events", []) or []
            conv_summary: dict[str, int] = {}
            if events:
                open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
                now_min = now_et.hour * 60 + now_et.minute
                # Real minutes (negative pre-market) — rvol_from_profile handles <0.
                minutes_since_open = now_min - open_min
                conv_summary = await process_events(
                    broker,
                    events,
                    today_et=today_et,
                    minutes_since_open=minutes_since_open,
                )
            logger.info(
                "options_flow intraday: date=%s prints=%s blocks=%s uv1=%s pressure=%s "
                "events=%s convergence=%s solo=%s pushes=%s",
                today_et,
                summary.get("prints"),
                summary.get("blocks"),
                summary.get("uv1"),
                summary.get("pressure"),
                len(events),
                conv_summary.get("convergence", 0),
                conv_summary.get("solo", 0),
                conv_summary.get("pushes", 0),
            )
        except Exception:
            logger.exception("options_flow intraday: unexpected failure")

    async def sweep_loop(self) -> None:
        """Phase 3b consumer: stream prints → at-print DIR-1 → SWP-1 sweeps → events."""
        import db.index_pressure as ip_db
        import db.options_flow as of_db
        from signals.options_flow.constants import QUOTE_STALE_FOR_DIR1_SECONDS
        from signals.options_flow.convergence import process_events
        from signals.options_flow.intraday import classify_direction, tag_likely_spreads
        from signals.options_flow.opening_pressure import WINDOWS, PressureGauge
        from signals.options_flow.rolling import RollingFlow
        from signals.options_flow.sweeps import SweepBuffer
        from trading.market_calendar import is_early_close

        def _fresh_gauges() -> dict[str, PressureGauge]:
            return {w: PressureGauge(w) for w in WINDOWS}

        # Flush stops shortly after each window closes; the 5-min grace covers
        # late-arriving prints whose ts still falls inside the window.
        gauge_cutoff_min = {
            w: start + length + 5 for w, (start, length) in WINDOWS.items()
        }

        buffer = SweepBuffer()
        rolling = RollingFlow()
        gauges = _fresh_gauges()
        gauge_final: set[str] = set()  # windows finalized today (day row written)
        rolling_last_check = 0.0
        gauge_last_flush = 0.0
        buffer_day = ""
        half_day = False
        stale_ms = QUOTE_STALE_FOR_DIR1_SECONDS * 1000

        while True:
            await asyncio.sleep(1.0)
            try:
                stream = self.stream
                if stream is None:
                    continue

                batch: list[dict] = []
                while stream.collected_prints:
                    batch.append(stream.collected_prints.popleft())

                now_et = datetime.now(_ET)
                today_et = now_et.date().isoformat()
                if buffer_day != today_et:
                    buffer = SweepBuffer()
                    rolling = RollingFlow()
                    gauges = _fresh_gauges()
                    gauge_final = set()
                    half_day = is_early_close(now_et.date())
                    buffer_day = today_et

                if batch:
                    for p in batch:
                        direction = "undetermined"
                        basis = "none"
                        q = stream.quote_at(
                            str(p.get("occ_symbol") or ""),
                            int(p.get("ts_ms") or 0),
                        )
                        if q is not None:
                            bid, ask, q_ts_ms = q
                            if abs(int(p.get("ts_ms") or 0) - q_ts_ms) <= stale_ms:
                                direction = classify_direction(
                                    price=float(p.get("price") or 0), bid=bid, ask=ask
                                )
                                if direction != "undetermined":
                                    basis = "stream_quote"
                        p["direction"] = direction
                        p["direction_basis"] = basis
                    tag_likely_spreads(batch)
                    buffer.add(batch)
                    # D-1/D-2 rolling detectors + pressure gauges ride the same
                    # classified prints (gauges self-filter to window/universe).
                    # Coverage mode is stamped per print at ACCRUAL time (audit F2),
                    # from server-ACKED state — never rewritten by later flushes.
                    mode = (
                        "market_wide" if stream.acked_market_wide else "gauge_lane"
                    )
                    for p in batch:
                        rolling.add_print(p)
                        for gauge in gauges.values():
                            gauge.add_print(p, mode=mode)

                # burst/cluster checks step once a minute (1-min bucket granularity)
                now_mono = time.time()
                if now_mono - rolling_last_check >= 60.0:
                    rolling_last_check = now_mono
                    now_ms = int(now_mono * 1000)
                    try:
                        for ev in rolling.check_bursts(now_ms) + rolling.check_clusters(
                            now_ms
                        ):
                            of_db.insert_event(ev)
                    except Exception:
                        logger.exception("options_flow rolling: check failed")
                # Gauge flush on its own 10s cadence — the write is tiny (≤13 syms ×
                # 30 rows, last-write-wins upsert of in-memory running totals), and a
                # 60s flush would make the widget lag the tape by up to a minute
                # during the exact half hours the gauge exists for.
                if now_mono - gauge_last_flush >= 10.0:
                    gauge_last_flush = now_mono
                    now_min = now_et.hour * 60 + now_et.minute
                    for win, gauge in gauges.items():
                        if win in gauge_final:
                            continue
                        # Half days (13:00 close): there IS no 15:30-16:00 session —
                        # never record one; finalize the day row as session_type=half
                        # so the absence is interpretable forever (audit F5/F6).
                        if half_day and win == "close":
                            try:
                                ip_db.record_day(
                                    date=today_et,
                                    window=win,
                                    session_type="half",
                                    minutes_observed=0,
                                    syms=0,
                                    coverage_modes=None,
                                    writer_pid=os.getpid(),
                                    finalized=True,
                                )
                                gauge_final.add(win)
                            except Exception:
                                logger.exception(
                                    "options_flow pressure gauge: half-day record "
                                    "failed"
                                )
                            continue
                        finalize = now_min > gauge_cutoff_min[win]
                        try:
                            rows = gauge.minute_rows(today_et)
                            if rows:
                                ip_db.upsert_minutes(rows, window=win)
                            if rows or finalize:
                                stats = gauge.window_stats()
                                ip_db.record_day(
                                    date=today_et,
                                    window=win,
                                    session_type="full",
                                    minutes_observed=stats["minutes_observed"],
                                    syms=stats["syms"],
                                    coverage_modes=stats["coverage_modes"],
                                    writer_pid=os.getpid(),
                                    finalized=finalize,
                                )
                                if finalize:
                                    gauge_final.add(win)
                                    logger.info(
                                        "options_flow pressure gauge: %s window "
                                        "final — syms=%s minutes=%s/%s coverage=%s",
                                        win,
                                        stats["syms"],
                                        stats["minutes_observed"],
                                        WINDOWS[win][1],
                                        stats["coverage_modes"],
                                    )
                        except Exception:
                            logger.exception(
                                "options_flow pressure gauge: %s flush failed", win
                            )

                events = buffer.close_ready(int(time.time() * 1000), today_et)
                if not events:
                    continue

                try:
                    broker = get_broker("alpaca")
                except Exception:
                    logger.warning("options_flow sweeps: Alpaca broker unavailable")
                    broker = None

                if broker is not None:
                    await enrich_sweep_events(events, broker)

                for ev in events:
                    of_db.insert_event(ev)

                if broker is None:
                    continue
                open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
                now_min = now_et.hour * 60 + now_et.minute
                conv = await process_events(
                    broker,
                    events,
                    today_et=today_et,
                    # Real minutes (negative pre-market): rvol_from_profile returns None
                    # for <0 — clamping to 0 compared pre-market volume against the
                    # first 5-min bucket and fabricated 16-22x RVOL (2026-07-10 4AM).
                    minutes_since_open=now_min - open_min,
                )
                logger.info(
                    "options_flow sweeps: events=%s convergence=%s solo=%s pushes=%s",
                    len(events),
                    conv.get("convergence", 0),
                    conv.get("solo", 0),
                    conv.get("pushes", 0),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("options_flow sweeps: cycle failed")

    async def stream_run(self) -> None:
        """OPRA websocket transport (Phase 3a); prints feed `sweep_loop`."""
        if not broker_is_configured("alpaca"):
            logger.warning("options_flow stream: Alpaca not configured; not starting")
            return

        cfg = alpaca_config()
        key = str(cfg.get("api_key_id") or "").strip()
        secret = str(cfg.get("api_secret_key") or "").strip()
        if not key or not secret:
            logger.warning(
                "options_flow stream: missing Alpaca credentials; not starting"
            )
            return

        from signals.options_flow.stream import OptionsFlowStream

        stream = OptionsFlowStream(
            key=key,
            secret=secret,
            on_prints=self.on_prints,
            get_subscription_set=self.subscription_set,
            meta_for=self.meta_for,
            get_anchor_quotes=self.anchor_quotes,
        )
        self.stream = stream
        logger.info("options_flow stream: starting OPRA websocket (DARK collector)")
        try:
            await stream.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("options_flow stream: run loop exited unexpectedly")
        finally:
            self.stream = None

    def stop(self) -> None:
        stream = self.stream
        if stream is not None:
            stream.stop()
