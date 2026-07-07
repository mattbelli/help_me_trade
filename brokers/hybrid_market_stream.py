"""Per-asset-class live-stream hub (Seam B).

`HybridMarketStreamHub` composes the two existing upstream engines VERBATIM:
  • `AlpacaMarketStreamHub` — SIP stocks websocket (equities/ETFs)
  • `SchwabMarketStreamHub`  — Schwab level-one (futures only, /-prefixed)

Both children share the composite's `_quotes` and `_clients` dicts BY REFERENCE, so
every child tick lands in one quote table and every child broadcast fans out to the
composite's real browser clients. Subscription management partitions the desired set
by asset class and hands each child only its slice; each child keeps its own
`_subscribed`/`_pending_*` over that slice (no shared pending-set race) and runs its
own reconnect / suspect-print guard / throttle / WAF breaker unchanged.

Extension points (NOT wired this round — see the brief):
  • crypto — Alpaca crypto is a separate ws (wss://.../v1beta3/crypto/us) with its own
    auth+message shape; a whole new upstream loop, so it stays a documented skip.
  • option — OPRA needs contract-symbol handling, an options-quote UI, and separate
    entitlement confirmation; classified correctly but not streamed.
Both classes are simply not subscribed on the stream; their REST bars still route to
Alpaca via HybridMarketData.
"""

from __future__ import annotations

import asyncio

from brokers.market_stream import (
    MAX_WS_SYMBOLS,
    AlpacaMarketStreamHub,
    BaseMarketHub,
)
from brokers.schwab_market_stream import SchwabMarketStreamHub
from brokers.symbol_class import classify_symbol
from config.settings import broker_is_configured

# SIP allows unlimited websocket symbols; give the equity child a generous cap while
# keeping it finite. The Schwab (futures) child keeps the small default cap.
MAX_WS_SYMBOLS_ALPACA = 100


def _stream_partition(symbols: set[str]) -> tuple[set[str], set[str]]:
    """Split *symbols* into (alpaca_stream, schwab_stream) subscription slices.

    equity → Alpaca child, future → Schwab child. crypto/option are dropped
    (documented stream extension points; their REST bars still route to Alpaca).
    """
    alpaca: set[str] = set()
    schwab: set[str] = set()
    for s in symbols:
        cls = classify_symbol(s)
        if cls == "future":
            schwab.add(s)
        elif cls == "equity":
            alpaca.add(s)
        # TODO(crypto): route to an Alpaca crypto upstream ws once wired.
        # TODO(options): route to the OPRA options stream once entitled + UI exists.
    return alpaca, schwab


class HybridMarketStreamHub(BaseMarketHub):
    """Composite hub: Alpaca SIP equities + Schwab futures, one client fan-out."""

    def __init__(self) -> None:
        super().__init__()
        self._alpaca = AlpacaMarketStreamHub()
        self._schwab = SchwabMarketStreamHub()
        # Share the quote table + client registry into both children BY REFERENCE:
        # child ticks write to the one _quotes, child broadcasts iterate the one
        # _clients. Everything else (throttle, prev-prices, suspect-guard, pending
        # sets) stays per-child over its disjoint symbol slice.
        # NB: the broadcast-throttle bucket (_last_broadcast_ms/_pending_broadcast) is
        # NOT shared, so a symbol touched by BOTH a child tick and this hub's own
        # apply_extended() throttles through two independent 100ms windows (up to ~2x
        # the 10Hz/symbol ceiling). Harmless in practice — apply_extended only fires on
        # the slow ~30-60s REST ext-poll, so real overlap with a tick is rare.
        for child in (self._alpaca, self._schwab):
            child._quotes = self._quotes
            child._clients = self._clients

    def _live_flag(self) -> bool:
        # Equities are the bulk of the watchlist; liveness follows Alpaca (SIP).
        return broker_is_configured("alpaca")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Drive both children's upstream loops directly (each is a headless engine).
        for child in (self._alpaca, self._schwab):
            child._running = True
            child._upstream_task = asyncio.create_task(child._upstream_loop())

    async def stop(self) -> None:
        self._running = False
        await self._alpaca.stop()
        await self._schwab.stop()
        # Cancel any composite-level trailing broadcasts (from apply_extended).
        for task in list(self._pending_broadcast.values()):
            task.cancel()
        self._pending_broadcast.clear()
        self._last_broadcast_ms.clear()

    async def _ensure_baselines(self, symbols: set[str]) -> None:
        alpaca_syms, schwab_syms = _stream_partition(symbols)
        if alpaca_syms:
            await self._alpaca._ensure_baselines(alpaca_syms)
        if schwab_syms:
            await self._schwab._ensure_baselines(schwab_syms)

    async def _sync_upstream(self) -> None:
        desired = self._all_symbols()
        alpaca_syms, schwab_syms = _stream_partition(desired)
        await self._sync_child(self._alpaca, alpaca_syms, MAX_WS_SYMBOLS_ALPACA)
        await self._sync_child(self._schwab, schwab_syms, MAX_WS_SYMBOLS)

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
            desired = set(sorted(desired)[:cap])
        async with child._lock:
            to_add = desired - child._subscribed
            to_remove = child._subscribed - desired
            child._subscribed = desired
            child._pending_add |= to_add
            child._pending_remove |= to_remove
        if to_add:
            await child._ensure_baselines(to_add)
