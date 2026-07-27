"""Databento OPRA live transport — a drop-in alternative to `stream.py`.

Same duck-typed surface as ``OptionsFlowStream`` (``collected_prints``,
``quote_at``, ``acked_market_wide``, ``dropped_prints``, ``run``, ``stop``), so
`runner.sweep_loop` and every detector downstream are untouched. Selected by
``options_flow.provider`` in settings; **the Alpaca stream stays the default and
is not deleted** — this module is the thing that gets retired if we swap back.

Why it exists (measured 2026-07-26): Alpaca's OPRA quote budget caps at 1,000
contracts, which is ~21 per name across 40 names ≈ 0.3% of a chain, and the
reactive anchor set is cold at the open — 41.8% of opening-30-minute sweeps carry
no side at all, 57.4% classified overall. Databento's ``tcbbo`` carries the NBBO
**on the print itself**, so classification is 89% of prints / 92.7% of premium and
is flat across the session (58.1% open vs 57.3% rest). There is no quote budget,
no anchor pre-positioning and no hot pool here, because there is nothing to ration.

Two deliberate constraints:
  * **One variable changes.** Same watchlist, same detectors, same thresholds — the
    only difference is where prints come from. Widening to whole-market is a
    separate step; if source and scope moved together a regression could not be
    attributed to either.
  * **No auto-fallback.** A dropped session fails loudly rather than silently
    degrading to the worse source. Silent degradation is what left the copilot
    web-blind three times (07-02, 07-14, 07-25); a quiet downgrade in direction
    quality would be the same failure wearing different clothes.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, Callable

from signals.options_flow.intraday import normalize_print
from trading.market_hours import is_market_open

logger = logging.getLogger(__name__)

DATASET = "OPRA.PILLAR"
SCHEMA = "tcbbo"
UNDEF_PX = 9223372036854775807  # DBN sentinel for "no price"
PX_SCALE = 1e9
# Stamped onto every print so post-swap events are distinguishable from the
# Alpaca era in the SAME options_flow_events table. Without it the two
# classification regimes pool silently and any history that spans the swap is
# quietly meaningless — the lesson `schema_version` encodes for the corpus.
QUOTE_BASIS = "dbn_nbbo"
# Mirrors OptionsFlowStream.collected_prints (stream.py:156).
COLLECTED_PRINTS_MAX = 5000
# How often to re-check an empty watchlist. Cheap (an in-process poll-state
# read), and 30s is well inside the pre-open warm window it is waiting for.
WATCHLIST_WAIT_S = 30.0

# --- liveness (roadmap 0.1) --------------------------------------------------
# Without these a dead feed and a quiet market are indistinguishable, which is
# the arming precondition this lane was missing. `client.is_connected()` is not
# enough on its own: it reports the transport's opinion of itself, and the
# failure that matters is the one where the socket looks fine and no records
# arrive. So liveness is judged on DATA, not on connection state.
#
# Roadmap 0.1 requires a killed socket to raise inside 2 minutes. The flush tick
# is 1s, so 90s detects well inside that. It is also far longer than any real gap
# on a whole-chain parent subscription during RTH -- a 40-name subscription
# carries a large share of a tape that runs millions of prints a session.
#
# ⚠️ Judged on `data_msgs`, NOT `msgs`. The first version of this used `msgs` and
# was dead on arrival: databento dispatches gateway heartbeats (default 30s) to
# the same callback, so `msgs` advances forever through a totally dead data feed
# and this threshold could never trip. Caught in review 2026-07-27 before arming.
#
# What this uniquely covers is narrower than it looks, and worth stating: a HARD
# socket kill is already caught at ~41s by databento's own heartbeat monitor
# (heartbeat 30s + CLIENT_TIMEOUT_MARGIN 10s), which flips `is_connected()` and
# takes the pre-existing exit below. This clock is what catches the cases that
# monitor cannot see -- a gateway-side data stall on a healthy session, or a
# wedged callback thread (a blocking meta lookup), where the client's own monitor
# runs on the same blocked loop and never fires.
SILENCE_S = 90.0
# Subscription echo: catches "we asked for 40 parents and the server acked
# nothing", which is otherwise identical to a quiet market. symbology_map is
# per-Live-instance and written only from SymbolMappingMsg on this session
# (verified in the client), so a non-empty map is genuinely OUR ack.
# ⚠️ Two limits, both known and neither fixed here. (1) It proves SOME
# subscription was acked, not the whole list -- subscribe() batches 40 symbols
# into several requests and a partial rejection still sets acked. (2) Whether
# mappings arrive off-hours at all is UNVERIFIED against a real socket, which is
# why the off-hours branch logs instead of raising; see check().
SUBSCRIPTION_ACK_S = 60.0
# Transport alive, nothing usable coming out: meta lookups missing or symbology
# not resolving. Logged loudly but NOT fatal -- tearing down a healthy socket is
# the wrong response to a mapping problem, and it is a different bug with a
# different fix than a dead feed.
PIPELINE_STALL_S = 300.0
RATE_LOG_S = 60.0


class FeedDead(RuntimeError):
    """The transport stopped delivering. Raised so the failure cannot be mistaken
    for a clean shutdown. Never caught and retried in here: no auto-fallback is a
    deliberate property of this lane.

    ⚠️ Know what raising actually costs before adding one. The deployed owner is
    `hmt-flow-collector` (`OPTIONS_FLOW_EXTERNAL_COLLECTOR=1`, so `api/main.py`
    does NOT run the stream in-process). `runner._stream_run_databento` catches
    and logs the traceback, then `stream_run` returns — and `tools/flow_collector`
    awaits its legs FIRST_COMPLETED, so the whole collector exits and systemd
    restarts it 3s later. A raise is therefore a process restart, not just a log
    line, and a condition that re-raises every cycle is an unbounded restart loop.
    """


class FeedLiveness:
    """Data-driven liveness for the OPRA feed.

    Pure and clock-injected so the failure paths are testable without a socket.
    Tracks three things that fail independently:
      * the subscription was never acked,
      * records stopped arriving (dead transport),
      * records arrive but produce no prints (dead pipeline).
    """

    def __init__(self, now: float) -> None:
        self._last_msg_at = now
        self._last_print_at = now
        self._started_at = now
        self._msgs = 0
        self._prints = 0
        self._acked = False
        self._rate_at = now
        self._rate_prints = 0
        self._pipeline_warned = False

    def observe(self, *, data_msgs: int, prints: int, acks: int,
                now: float) -> None:
        """``data_msgs`` must be records carrying market data, NOT every record
        the callback sees -- gateway heartbeats arrive every 30s and would keep
        this clock alive through a totally dead feed.

        ``acks`` is the gateway's own ``SystemCode.SUBSCRIPTION_ACK`` count. It
        used to be ``mapped`` -- the size of ``symbology_map`` -- and that was
        simply the wrong signal; see `check`.
        """
        if data_msgs > self._msgs:
            self._last_msg_at = now
        if prints > self._prints:
            self._last_print_at = now
            self._pipeline_warned = False
        self._msgs, self._prints = data_msgs, prints
        if acks > 0:
            self._acked = True

    def idle(self, now: float) -> None:
        """Market is closed: restart the silence clocks.

        Without this, the first tick after the opening bell compares against a
        timestamp from the previous session and reports a dead feed on a feed
        that is working perfectly.
        """
        self._last_msg_at = self._last_print_at = now
        # Clearing the latch matters as much as the clocks: prints never advance
        # while the pipeline is broken, so without this a fault reported once on
        # Monday is never reported again on Tuesday, Wednesday or ever.
        self._pipeline_warned = False

    def check(self, *, now: float, market_open: bool) -> float | None:
        """Raise `FeedDead` on a dead transport.

        Returns the pipeline-stall duration when records are arriving but no
        prints come out of them — a *different* fault with a different fix, so it
        is returned for the caller to log with its own counters rather than being
        fatal. Returns None when healthy. Reported once per stall, not per tick.
        """
        # Fatal at any hour, and that is a MEASURED change, not a tidy-up.
        #
        # This used to infer the ack from `len(symbology_map)` and could only
        # raise during RTH, because nobody had checked whether mappings arrive
        # off-hours. Probed against the real gateway on 2026-07-27 at 00:07 ET
        # (tools/dbn_live_probe.py): the two halves of that premise came apart.
        # The gateway acks in ~17ms, off-hours included -- "system message
        # code=subscription_ack msg='Subscription request 0 for tcbbo data
        # succeeded'" -- but `symbology_map` stayed EMPTY for the full 75s,
        # because mappings are emitted for instruments that trade.
        #
        # So the old check was reading a signal that is absent by design when
        # the market is shut, and it duly fired a false "subscription never
        # acked" at t+60s on a subscription the server had already accepted.
        # The RTH-only hedge was load-bearing -- without it that false positive
        # would have restarted the collector every 60s all weekend. Keyed on the
        # real ack it is true at every hour, so the hedge is gone and a genuinely
        # rejected subscription is now caught at 03:00 instead of at 09:30.
        if not self._acked and now - self._started_at > SUBSCRIPTION_ACK_S:
            raise FeedDead(
                f"the gateway never acked the subscription "
                f"{now - self._started_at:.0f}s after subscribe "
                f"(data_msgs={self._msgs}). Measured latency for a good one is "
                f"~17ms at any hour, so this is a rejection, not a quiet market."
            )
        if not market_open:
            self.idle(now)
            return None
        silent = now - self._last_msg_at
        if silent > SILENCE_S:
            raise FeedDead(
                f"no records for {silent:.0f}s during RTH "
                f"(msgs={self._msgs} prints={self._prints}). The transport is "
                f"dead — a quiet market still delivers quotes."
            )
        stalled = now - self._last_print_at
        if stalled > PIPELINE_STALL_S and not self._pipeline_warned:
            self._pipeline_warned = True
            return stalled
        return None

    def take_rate(self, now: float) -> float | None:
        """Prints per minute since the last call, once per `RATE_LOG_S`.

        None between windows, so the caller can log unconditionally on the truthy
        result. The counter is the observability half of 0.1: a rate trending to
        zero is visible in the log before any threshold fires.
        """
        elapsed = now - self._rate_at
        if elapsed < RATE_LOG_S:
            return None
        rate = (self._prints - self._rate_prints) * 60.0 / max(elapsed, 1e-9)
        self._rate_at, self._rate_prints = now, self._prints
        return rate


class DatabentoFlowStream:
    """OPRA prints with the NBBO already attached, from Databento Live."""

    def __init__(
        self,
        *,
        key: str,
        on_prints: Callable[[list[dict]], None],
        get_subscription_set: Callable[[], list[str]],
        meta_for: Callable[[str], dict | None],
        flush_interval_s: float = 1.0,
        replay_from: Any = None,
    ) -> None:
        self._key = key
        self._on_prints = on_prints
        self._get_subscription_set = get_subscription_set
        self._meta_for = meta_for
        self._flush_interval_s = flush_interval_s
        # Intraday replay: the live gateway will resend the last ≤24h from a
        # given start time. None in production. It exists so the arming check
        # can push a REAL session through this exact code path on a weeknight
        # instead of waiting for an open — and because replay arrives as fast
        # as the gateway will send it, it is also the only honest way to
        # exercise the slow-reader path without a market.
        self._replay_from = replay_from

        self.collected_prints: deque[dict] = deque(maxlen=COLLECTED_PRINTS_MAX)
        self.dropped_prints = 0
        self.prints = 0
        # Every record dispatched to us, INCLUDING gateway heartbeats and symbol
        # mappings. Diagnostic only -- never a liveness signal, see _on_record.
        self.msgs = 0
        # Records carrying a book level: the feed actually delivering market data.
        # This is what liveness is judged on.
        self.data_msgs = 0
        self.skipped_no_meta = 0
        self.unresolved_symbols = 0
        # Control-plane counters (added 2026-07-27 from the vendor docs). Each
        # one is a condition the gateway reports and we were not reading, and
        # every one of them makes the tape WRONG without making it STOP — which
        # is the failure class the liveness clocks cannot see.
        self.subscription_acks = 0
        self.slow_reader_warnings = 0
        self.skipped_by_gateway = 0
        self.symbol_resolution_failures = 0
        self.callback_errors = 0
        self.reconnects = 0
        self.reconnect_gap_s = 0.0
        # Databento subscribes by parent symbol, so coverage is every contract on
        # every watched name — not a rationed slice. It is not the "*" market-wide
        # subscription the Alpaca lane means by this flag, so it stays False.
        self.acked_market_wide = False

        self._pending: list[dict] = []
        self._pending_lock = threading.Lock()
        self._client: Any = None
        self._stopping = False

    # --- the surface runner.sweep_loop consumes ----------------------------

    def quote_at(self, occ: str, ts_ms: int) -> tuple[float, float, int] | None:
        """Always None: this lane keeps NO quote store to look a print up in.

        Part of the duck-typed surface only. A tcbbo record IS a print plus the
        NBBO that stood behind it, so the quote is stamped onto the print at
        ingest (``nbbo``) and `sweep_loop` reads it from there. `occ` and `ts_ms`
        are the Alpaca lane's question — "which sampled quote was prevailing at
        this timestamp" — and it has no meaning here. When a print reaches the
        runner without an ``nbbo`` its book was UNDEF on the wire, and None is
        the honest answer rather than a neighbouring print's quote.
        """
        return None

    def stop(self) -> None:
        self._stopping = True
        client = self._client
        if client is not None:
            try:
                client.stop()
            except Exception:
                logger.exception("dbn stream: stop failed")

    # --- ingest -------------------------------------------------------------

    def _on_record(self, rec: Any) -> None:
        self.msgs += 1
        levels = getattr(rec, "levels", None)
        if not levels:
            self._on_control(rec)
            return
        # Counted AFTER the levels guard, and that placement is the whole point:
        # databento dispatches EVERY record to this callback, including the
        # gateway's 30s heartbeat (protocol.py calls received_record outside the
        # SystemMsg branch). Judging silence on `msgs` therefore proves only that
        # the socket is warm — it advances forever through a completely dead data
        # feed. `data_msgs` counts records that actually carry a book level, so a
        # stalled feed goes quiet here even while heartbeats continue.
        self.data_msgs += 1
        client = self._client
        if client is None:
            return
        try:
            occ = client.symbology_map.get(rec.instrument_id)
        except Exception:
            occ = None
        if not occ:
            self.unresolved_symbols += 1
            return
        occ = str(occ).strip().upper()

        meta = self._meta_for(occ)
        if meta is None:
            self.skipped_no_meta += 1
            return

        raw = {
            "t": datetime.fromtimestamp(rec.ts_recv / 1e9, UTC).isoformat(),
            "p": rec.price / PX_SCALE,
            "s": rec.size,
            "x": str(getattr(rec, "publisher_id", "") or ""),
            "c": [],
        }
        normalized = normalize_print(raw, meta=meta)
        if normalized is None:
            return
        normalized["quote_basis"] = QUOTE_BASIS
        # The book travels ON the print rather than in a store keyed by
        # (occ, ts_ms). Do not re-grow the store — it replaced one on 07-27 and
        # a lock would have fixed only the first of its three problems:
        #   * racy — databento.Live runs its own loop on its own thread
        #     (client.py:78-80), so this callback and sweep_loop's read ran
        #     concurrently over a deque; sweep_loop catches the resulting
        #     RuntimeError AFTER popleft, discarding that whole cycle's prints.
        #   * inexact, and dishonestly so — ts_ms truncates nanoseconds, so two
        #     prints on one contract in the same millisecond are ONE key and
        #     both got the first book of that ms under a quote_basis that
        #     claims the print's own NBBO. Same-ms repeats are the sweeps this
        #     lane exists to classify (~0.8% of prints got a different book).
        #   * unbounded — it had no eviction at all, unlike the Alpaca ring.
        bid, ask = levels[0].bid_px, levels[0].ask_px
        if bid != UNDEF_PX and ask != UNDEF_PX and 0 < bid < ask:
            normalized["nbbo"] = (
                bid / PX_SCALE, ask / PX_SCALE, int(rec.ts_recv // 1_000_000)
            )
        self.prints += 1
        with self._pending_lock:
            self._pending.append(normalized)

    def _on_control(self, rec: Any) -> None:
        """Non-data records: the gateway telling us something about the session.

        Off the hot path by construction — this runs only for records with no
        book, which is heartbeats and control messages. The two slow-reader
        codes are the reason it exists: a client that falls behind does not stop
        receiving data, so neither the silence clock nor the pipeline clock can
        see it, and the tape quietly stops being complete or stops being live.
        """
        code = getattr(rec, "code", None)
        if code is None:
            return
        from databento_dbn import ErrorCode, SystemCode

        name = type(rec).__name__
        if name.startswith("SystemMsg"):
            if code == SystemCode.SUBSCRIPTION_ACK:
                # The gateway's own ack, as opposed to inferring one from the
                # first symbol mapping. Counted rather than acted on until the
                # live-socket check shows whether it arrives outside RTH.
                self.subscription_acks += 1
            elif code == SystemCode.SLOW_READER_WARNING:
                self.slow_reader_warnings += 1
                logger.warning(
                    "dbn stream: gateway says we are a SLOW READER (%s warnings)"
                    " — records are still all being sent, but we are behind real"
                    " time and the prints we classify are stale.",
                    self.slow_reader_warnings,
                )
        elif name.startswith("ErrorMsg"):
            if code == ErrorCode.SKIPPED_RECORDS_AFTER_SLOW_READING:
                # Only reachable if slow_reader_behavior is ever set to "skip".
                # Loud because it is unrecoverable data loss: the corpus and the
                # live lane would disagree and nothing else would say why.
                self.skipped_by_gateway += 1
                logger.error(
                    "dbn stream: the GATEWAY DROPPED RECORDS to let us catch up "
                    "(%s events). Prints are missing and cannot be recovered.",
                    self.skipped_by_gateway,
                )
            elif code == ErrorCode.SYMBOL_RESOLUTION_FAILED:
                # Since 2026-04-26 an unresolvable symbol no longer kills the
                # session, so a bad name in a dynamic watchlist now fails by
                # simply never streaming. This counter is the only trace.
                self.symbol_resolution_failures += 1
                logger.error(
                    "dbn stream: symbol resolution FAILED (%s) — that name is "
                    "not streaming and the session did not stop: %s",
                    self.symbol_resolution_failures,
                    getattr(rec, "err", ""),
                )

    def _on_reconnect(self, gap_start: Any, gap_end: Any) -> None:
        """The session dropped and databento rebuilt it, replaying every
        subscription. Loud anyway — self-healing is exactly how a chronically
        flapping session hides, and these seconds are prints we never saw.
        """
        self.reconnects += 1
        if self._replay_from is not None:
            # Unreachable while run() disables the policy for replay; kept
            # because the consequence is a silently wrong measurement rather
            # than an error, and it must never come back quietly.
            logger.error(
                "dbn stream: RECONNECT DURING REPLAY — databento re-subscribes "
                "with start=None, so this session is now LIVE, not a replay of "
                "%s. Anything measured after this point is the current tape.",
                self._replay_from,
            )
        try:
            gap = float((gap_end - gap_start).total_seconds())
        except Exception:
            gap = 0.0
        self.reconnect_gap_s += gap
        logger.error(
            "dbn stream: RECONNECTED after a %.1fs gap (%s this session, %.1fs "
            "total). Subscriptions were replayed; the prints inside the gap are "
            "gone. A sweep cluster straddling it closes with legs missing.",
            gap, self.reconnects, self.reconnect_gap_s,
        )

    def _on_callback_error(self, exc: BaseException) -> None:
        """Count exceptions out of `_on_record`; databento already logs each one.

        Without this they are log lines only. The count is what makes a
        systematic failure legible — every record raising looks exactly like a
        dead pipeline in the print rate, and this separates the two.
        """
        self.callback_errors += 1

    def _drain(self) -> list[dict]:
        with self._pending_lock:
            batch, self._pending = self._pending, []
        return batch

    def _parent_symbols(self) -> list[str]:
        return [
            f"{s.strip().upper()}.OPT"
            for s in (self._get_subscription_set() or [])
            if str(s).strip()
        ]

    async def run(self) -> None:
        # WAIT for a watchlist; never return because it is empty.
        #
        # `runner.subscription_set()` returns [] whenever the flow session is
        # closed, which is most of the day. Returning here ends the flow-stream
        # leg, `tools/flow_collector` awaits its legs FIRST_COMPLETED, so the
        # whole collector exits and systemd (Restart=always, RestartSec=3)
        # brings it straight back to do it again. Arming at 06:27 ET would have
        # been ~3,000 restarts before the pre-open warm window opened. The
        # Alpaca lane never had this: its run() is a `while not self._stopped`
        # loop that simply sits there with nothing subscribed.
        symbols = self._parent_symbols()
        if not symbols:
            logger.info(
                "dbn stream: watchlist empty (outside the session window) — "
                "waiting, not exiting."
            )
            while not symbols and not self._stopping:
                await asyncio.sleep(WATCHLIST_WAIT_S)
                symbols = self._parent_symbols()
        if self._stopping:
            return

        # Imported only once there is something to subscribe: databento is not
        # in requirements.txt, so the wait above stays importable without it.
        import databento as dbn

        # slow_reader_behavior is passed EXPLICITLY, not left to the default.
        # Databento flipped that default from "warn" to "skip" on 2026-04-26 for
        # the stateless schemas (mbp-1, bbo-*, cbbo-*); tcbbo is not on that list
        # today, but relying on a vendor default that has already moved once is
        # how a lane starts losing prints without a diff. "warn" is also the
        # right answer for this lane on the merits: under "skip" the gateway
        # drops trades to catch us up, and a directional-flow measurement whose
        # denominator silently shrinks under load is worse than a late one.
        # Headroom measured 2026-07-27 on real records: _on_record runs at
        # ~90k rec/s through the real meta_for miss path, against a whole-market
        # OPRA tcbbo peak of 6,642 rec/s (1s) — ~13x, so this should stay theory.
        #
        # reconnect_policy defaults to NONE. We set RECONNECT because the
        # alternative was never "stay down" — it was "exit and let systemd
        # restart the collector in 3s", which throws away the in-memory
        # SweepBuffer, RollingFlow and pressure gauges to recover from a blip.
        # databento's reconnect REPLAYS every subscription (session.py:737-783)
        # and hands us the exact gap boundaries, so the process keeps its state.
        # Reconnecting to the SAME source is not the silent downgrade the
        # no-fallback rule forbids; degrading to Alpaca would be.
        #
        # ⚠️ It also makes `is_connected()` useless as a health signal: with a
        # policy set, `is_disconnected()` returns `_reconnect_task.done()`, so
        # it reads True right through an outage of any length. The 90s data
        # silence clock below is therefore the ONLY thing that catches a session
        # that never comes back — which is safe only because that clock was
        # fixed on 07-27 (it used to be defeated by heartbeats). The
        # is_connected() check further down still matters: it fires when the
        # reconnect task itself gives up, which is the terminal case.
        # ⚠️ Reconnect is DISABLED for replay, and this is not tidiness.
        # databento's reconnect re-subscribes with `start=None` hardcoded
        # (session.py:762-769), so a replay session that drops comes back as a
        # LIVE one. Caught 2026-07-27 by asking for a Friday outside the replay
        # window: the gateway rejected it and closed, reconnect fired 15ms later
        # with start='now', and the probe then reported a healthy session —
        # ack received, zero data — with `reconnects=1` as the only trace. That
        # is a replay run silently measuring an empty overnight tape while
        # looking like it measured the session. Production sets no replay_from
        # and keeps reconnect.
        client = dbn.Live(
            key=self._key,
            slow_reader_behavior="warn",
            reconnect_policy="none" if self._replay_from else "reconnect",
        )
        client.add_callback(self._on_record, self._on_callback_error)
        client.add_reconnect_callback(self._on_reconnect)
        client.subscribe(
            dataset=DATASET, schema=SCHEMA, stype_in="parent", symbols=symbols,
            start=self._replay_from,
        )
        self._client = client
        client.start()
        logger.info(
            "dbn stream: OPRA live started — %d parent symbols, schema=%s",
            len(symbols), SCHEMA,
        )

        live = FeedLiveness(time.monotonic())
        try:
            while not self._stopping:
                await asyncio.sleep(self._flush_interval_s)
                batch = self._drain()
                if batch:
                    self._on_prints(batch)

                # Liveness is judged on records, not on is_connected() — the
                # failure that matters is a socket that still reports healthy
                # while nothing arrives. monotonic() so an NTP step cannot
                # manufacture or mask a stall.
                now = time.monotonic()
                try:
                    mapped = len(client.symbology_map)
                except Exception:
                    mapped = 0
                live.observe(data_msgs=self.data_msgs, prints=self.prints,
                             acks=self.subscription_acks, now=now)
                if self._stopping:
                    break
                stalled = live.check(now=now, market_open=is_market_open())
                if stalled is not None:
                    logger.error(
                        "dbn stream: records flowing but NO prints for %.0fs "
                        "during RTH (msgs=%s unresolved_symbols=%s "
                        "skipped_no_meta=%s). The socket is alive and the "
                        "pipeline is not — check symbology and meta, not the "
                        "connection.",
                        stalled, self.data_msgs, self.unresolved_symbols,
                        self.skipped_no_meta,
                    )
                rate = live.take_rate(now)
                if rate is not None and is_market_open():
                    # RTH only: overnight this would emit ~1,050 lines of
                    # "0.0 prints/min" a day and bury the one reading that
                    # matters -- the rate decaying while the market is open.
                    logger.info(
                        "dbn stream: %.1f prints/min (data_msgs=%s msgs=%s "
                        "prints=%s mapped=%s acks=%s slow=%s dropped=%s "
                        "unresolved_syms=%s cb_errors=%s reconnects=%s "
                        "gap_s=%.1f)",
                        rate, self.data_msgs, self.msgs, self.prints, mapped,
                        self.subscription_acks, self.slow_reader_warnings,
                        self.skipped_by_gateway,
                        self.symbol_resolution_failures, self.callback_errors,
                        self.reconnects, self.reconnect_gap_s,
                    )

                if not client.is_connected():
                    # Terminal only. Under reconnect_policy="reconnect" this is
                    # False only once the reconnect task itself has finished —
                    # i.e. databento stopped trying — so a transient drop never
                    # reaches here, it goes to _on_reconnect. Loud, not silent:
                    # the Alpaca lane is one settings flip away and a human
                    # should make that call, not a fallback branch.
                    logger.error(
                        "dbn stream: session is down and databento stopped "
                        "reconnecting (msgs=%s prints=%s reconnects=%s) — NOT "
                        "falling back; set options_flow.provider=alpaca to revert",
                        self.msgs, self.prints, self.reconnects,
                    )
                    return
        finally:
            self._client = None
            try:
                client.stop()
            except Exception:
                logger.exception("dbn stream: client stop failed on exit")
