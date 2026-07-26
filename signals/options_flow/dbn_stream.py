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
from collections import deque
from datetime import UTC, datetime
from typing import Any, Callable

from signals.options_flow.constants import QUOTE_STALE_FOR_DIR1_SECONDS
from signals.options_flow.intraday import normalize_print

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
        self.msgs = 0
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

        try:
            while not self._stopping:
                await asyncio.sleep(self._flush_interval_s)
                batch = self._drain()
                if batch:
                    self._on_prints(batch)
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
