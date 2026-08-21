"""Per-asset-class live-stream hub (Seam B).

`HybridMarketStreamHub` composes the three existing upstream engines VERBATIM:
  • `AlpacaMarketStreamHub` — SIP stocks websocket (equities/ETFs)
  • `SchwabMarketStreamHub`  — Schwab level-one (futures only, /-prefixed)
  • `AlpacaCryptoStreamHub`  — Alpaca v1beta3 crypto websocket (BTC/USD…), its own
    connection, never the equity collector proxy

All three children share the composite's `_quotes` and `_clients` dicts BY REFERENCE,
so every child tick lands in one quote table and every child broadcast fans out to the
composite's real browser clients. Subscription management partitions the desired set
by asset class and hands each child only its slice; each child keeps its own
`_subscribed`/`_pending_*` over that slice (no shared pending-set race) and runs its
own reconnect / suspect-print guard / throttle / WAF breaker unchanged.

Extension point (NOT wired this round):
  • option — OPRA needs contract-symbol handling, an options-quote UI, and separate
    entitlement confirmation; classified correctly but not streamed. Options are
    simply not subscribed on the stream and have no REST bar route either.
"""

from __future__ import annotations

import asyncio
import logging

from brokers.market_stream import (
    MAX_WS_SYMBOLS,
    AlpacaCryptoStreamHub,
    AlpacaMarketStreamHub,
    BaseMarketHub,
)
from brokers.schwab_market_stream import SchwabMarketStreamHub
from brokers.symbol_class import classify_symbol
from config.settings import broker_is_configured

logger = logging.getLogger(__name__)

# SIP allows unlimited websocket symbols; the cap only bounds OUR per-print work
# (tape detector + broadcast fan-out), so keep it far above realistic usage.
# Real usage is pinned watchlists/alerts (~30) + every open-position symbol the
# positions widget subscribes (~100 for the momentum book): 100 was silently
# exceeded on 2026-07-13 and the alphabetical cut starved TSLA/XLE/XLF.
# The Schwab (futures) child keeps the small default cap.
MAX_WS_SYMBOLS_ALPACA = 500


def _stream_partition(symbols: set[str]) -> tuple[set[str], set[str], set[str]]:
    """Split *symbols* into (alpaca_stream, schwab_stream, crypto_stream) slices.

    equity → Alpaca child, future → Schwab child, crypto → Alpaca crypto child.
    Options are dropped (documented stream extension point).
    """
    alpaca: set[str] = set()
    schwab: set[str] = set()
    crypto: set[str] = set()
    for s in symbols:
        cls = classify_symbol(s)
        if cls == "future":
            schwab.add(s)
        elif cls == "equity":
            alpaca.add(s)
        elif cls == "crypto":
            crypto.add(s)
        # TODO(options): route to the OPRA options stream once entitled + UI exists.
    return alpaca, schwab, crypto


class HybridMarketStreamHub(BaseMarketHub):
    """Composite hub: Alpaca SIP equities + Schwab futures + Alpaca crypto,
    one client fan-out."""

    def __init__(self) -> None:
        super().__init__()
        self._alpaca = AlpacaMarketStreamHub()
        self._schwab = SchwabMarketStreamHub()
        self._crypto = AlpacaCryptoStreamHub()
        # Share the quote table + client registry into every child BY REFERENCE:
        # child ticks write to the one _quotes, child broadcasts iterate the one
        # _clients. Everything else (throttle, prev-prices, suspect-guard, pending
        # sets) stays per-child over its disjoint symbol slice.
        # NB: the broadcast-throttle bucket (_last_broadcast_ms/_pending_broadcast) is
        # NOT shared, so a symbol touched by BOTH a child tick and this hub's own
        # apply_extended() throttles through two independent 100ms windows (up to ~2x
        # the 10Hz/symbol ceiling). Harmless in practice — apply_extended only fires on
        # the slow ~30-60s REST ext-poll, so real overlap with a tick is rare.
        for child in (self._alpaca, self._schwab, self._crypto):
            child._quotes = self._quotes
            child._clients = self._clients
            # One minute-bar authority: /api/bars splices from THIS composite's
            # ring, so both children must build into it.
            child.bars = self.bars
            child._last_print_ms = self._last_print_ms
            # One tape detector: the equity child's _on_trade tap must queue
            # into the deque the api drain loop reads off THIS composite.
            child.tape_detector = self.tape_detector

    def _live_flag(self) -> bool:
        # Equities are the bulk of the watchlist; liveness follows Alpaca (SIP).
        return broker_is_configured("alpaca")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Drive every child's upstream loop directly (each is a headless engine).
        for child in (self._alpaca, self._schwab, self._crypto):
            child._running = True
            child._upstream_task = asyncio.create_task(child._upstream_loop())

    async def stop(self) -> None:
        self._running = False
        await self._alpaca.stop()
        await self._schwab.stop()
        await self._crypto.stop()
        # Cancel any composite-level trailing broadcasts (from apply_extended).
        for task in list(self._pending_broadcast.values()):
            task.cancel()
        self._pending_broadcast.clear()
        self._last_broadcast_ms.clear()

    async def _ensure_baselines(self, symbols: set[str]) -> None:
        alpaca_syms, schwab_syms, crypto_syms = _stream_partition(symbols)
        if alpaca_syms:
            await self._alpaca._ensure_baselines(alpaca_syms)
        if schwab_syms:
            await self._schwab._ensure_baselines(schwab_syms)
        if crypto_syms:
            await self._crypto._ensure_baselines(crypto_syms)

    async def _sync_upstream(self) -> None:
        desired = self._all_symbols()
        alpaca_syms, schwab_syms, crypto_syms = _stream_partition(desired)
        await self._sync_child(self._alpaca, alpaca_syms, MAX_WS_SYMBOLS_ALPACA)
        await self._sync_child(self._schwab, schwab_syms, MAX_WS_SYMBOLS)
        await self._sync_child(self._crypto, crypto_syms, MAX_WS_SYMBOLS)

    async def _sync_child(
        self, child: BaseMarketHub, desired: set[str], cap: int
    ) -> None:
        """Diff *desired* against *child*'s subscription and push its pending sets.

        Mirrors BaseMarketHub._sync_upstream but scoped to one child's slice and cap,
        so the child's own upstream loop drains and (un)subscribes exactly its class.
        We never call child._sync_upstream() — that would re-derive the set from the
        SHARED _clients and pull in the other class's symbols.
        """
        if len(desired) > cap:
            # Over-cap: keep every pinned symbol (watchlists + price alerts —
            # dropping one silently freezes its quote everywhere), shed client
            # extras alphabetically, and say so: a silent cut cost a day of
            # debugging when it starved TSLA.
            pinned = desired & self._pinned
            extras = sorted(desired - pinned)[: max(0, cap - len(pinned))]
            kept = pinned | set(extras)
            logger.warning(
                "market stream cap: dropping %d of %d desired symbols "
                "(cap=%d, e.g. %s)",
                len(desired) - len(kept),
                len(desired),
                cap,
                ", ".join(sorted(desired - kept)[:8]),
            )
            desired = kept
        async with child._lock:
            to_add = desired - child._subscribed
            to_remove = child._subscribed - desired
            child._subscribed = desired
            child._pending_add |= to_add
            child._pending_remove |= to_remove
        if to_add:
            await child._ensure_baselines(to_add)
