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

from signals.options_flow.constants import QUOTE_STALE_FOR_DIR1_SECONDS
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
QUOTE_STALE_MS = QUOTE_STALE_FOR_DIR1_SECONDS * 1000

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
        self._ack_warned = False

    def observe(self, *, data_msgs: int, prints: int, mapped: int,
                now: float) -> None:
        """``data_msgs`` must be records carrying market data, NOT every record
        the callback sees -- gateway heartbeats arrive every 30s and would keep
        this clock alive through a totally dead feed."""
        if data_msgs > self._msgs:
            self._last_msg_at = now
        if prints > self._prints:
            self._last_print_at = now
            self._pipeline_warned = False
        self._msgs, self._prints = data_msgs, prints
        if mapped > 0:
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
        if not self._acked and now - self._started_at > SUBSCRIPTION_ACK_S:
            msg = (
                f"subscription never acked: no symbol mappings "
                f"{now - self._started_at:.0f}s after subscribe "
                f"(data_msgs={self._msgs}). The server did not accept what we "
                f"asked for; this is NOT a quiet market."
            )
            # Fatal only during RTH. The claim that mappings arrive on subscribe
            # regardless of market activity is UNVERIFIED against a real socket,
            # and the deployed owner is hmt-flow-collector, where any raise ends
            # the process and systemd restarts it in 3s. If the premise is wrong,
            # raising off-hours is an unbounded restart loop -- ~1,100 fresh live
            # sessions across a weekend, each one re-raising at t+60s. Logging
            # instead costs nothing: a subscription that failed overnight is
            # still caught at 09:30 by the silence check below.
            if not market_open:
                if not self._ack_warned:
                    self._ack_warned = True
                    logger.error("dbn stream: %s", msg)
            else:
                raise FeedDead(msg)
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
    ) -> None:
        self._key = key
        self._on_prints = on_prints
        self._get_subscription_set = get_subscription_set
        self._meta_for = meta_for
        self._flush_interval_s = flush_interval_s

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
        # Databento subscribes by parent symbol, so coverage is every contract on
        # every watched name — not a rationed slice. It is not the "*" market-wide
        # subscription the Alpaca lane means by this flag, so it stays False.
        self.acked_market_wide = False

        self._quote_ring: dict[str, deque[tuple[int, float, float]]] = {}
        self._pending: list[dict] = []
        self._pending_lock = threading.Lock()
        self._client: Any = None
        self._stopping = False

    # --- the surface runner.sweep_loop consumes ----------------------------

    def quote_at(self, occ: str, ts_ms: int) -> tuple[float, float, int] | None:
        """The NBBO that travelled with the print. Exact by construction — there is
        no staleness window to tune because the quote is not sampled separately."""
        ring = self._quote_ring.get(occ)
        if not ring:
            return None
        best: tuple[int, float, float] | None = None
        for entry_ts, bid, ask in ring:
            if entry_ts <= ts_ms and (best is None or entry_ts > best[0]):
                if ts_ms - entry_ts <= QUOTE_STALE_MS:
                    best = (entry_ts, bid, ask)
        if best is None:
            return None
        return best[1], best[2], best[0]

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

        ts_ms = int(rec.ts_recv // 1_000_000)
        bid, ask = levels[0].bid_px, levels[0].ask_px
        if bid != UNDEF_PX and ask != UNDEF_PX and 0 < bid < ask:
            ring = self._quote_ring.get(occ)
            if ring is None:
                ring = deque(maxlen=64)
                self._quote_ring[occ] = ring
            ring.append((ts_ms, bid / PX_SCALE, ask / PX_SCALE))

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
        self.prints += 1
        with self._pending_lock:
            self._pending.append(normalized)

    def _drain(self) -> list[dict]:
        with self._pending_lock:
            batch, self._pending = self._pending, []
        return batch

    async def run(self) -> None:
        import databento as dbn

        symbols = [
            f"{s.strip().upper()}.OPT"
            for s in (self._get_subscription_set() or [])
            if str(s).strip()
        ]
        if not symbols:
            logger.warning("dbn stream: empty watchlist; not starting")
            return

        client = dbn.Live(key=self._key)
        client.add_callback(self._on_record)
        client.subscribe(
            dataset=DATASET, schema=SCHEMA, stype_in="parent", symbols=symbols
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
                             mapped=mapped, now=now)
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
                        "prints=%s mapped=%s)",
                        rate, self.data_msgs, self.msgs, self.prints, mapped,
                    )

                if not client.is_connected():
                    # Loud, not silent: the Alpaca lane is one settings flip away
                    # and a human should make that call, not a fallback branch.
                    logger.error(
                        "dbn stream: session disconnected (msgs=%s prints=%s) — "
                        "NOT falling back; set options_flow.provider=alpaca to revert",
                        self.msgs, self.prints,
                    )
                    return
        finally:
            self._client = None
            try:
                client.stop()
            except Exception:
                logger.exception("dbn stream: client stop failed on exit")
