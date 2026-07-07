"""Per-asset-class REST market-data router (Seam A).

`HybridMarketData` implements the `MarketDataProvider` protocol by delegating each
symbol to a child provider chosen by `classify_symbol`: futures → Schwab, equities/ETFs
→ Alpaca SIP. Batch methods PARTITION the symbol list, call each child with only its
slice, and MERGE the results — so a mixed watchlist fans out to Alpaca (the ~480
req/min bar storm that trips Schwab's Akamai WAF) and leaves only the handful of
futures on Schwab. Each child leg is fault-isolated: if one provider fails, the other's
bars are still served (a Schwab hiccup must never black out the equity watchlist).

Crypto/option have no REST bar endpoint in our adapters yet (deferred), so they are
surfaced as an explicit gap — logged + skipped — rather than silently forwarded to the
stock endpoint (which would return quietly empty data).

Selected via `MARKET_DATA_PROVIDER=hybrid` (see brokers.factory). Deny-by-default:
the factory refuses to build this unless Alpaca is configured, so equities can never
silently fall back to Schwab and re-flood the WAF.

Children are built lazily through `get_market_data_provider` so its Schwab
token-mtime cache-heal still applies, and the Schwab child is only constructed when a
future is actually routed (a Schwab-less setup still serves equities).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from brokers.base import MarketDataProvider
from brokers.symbol_class import classify_symbol

if TYPE_CHECKING:
    from brokers.alpaca import StockBar

logger = logging.getLogger(__name__)


def _route(symbols: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split *symbols* into (equity_slice, future_slice, unsupported_slice), order-preserving.

    equity/ETF → Alpaca, future → Schwab. crypto/option have no REST bar endpoint yet,
    so they land in `unsupported` and are surfaced as a visible gap rather than
    silently routed to the wrong (stock) endpoint.
    """
    equity: list[str] = []
    future: list[str] = []
    unsupported: list[str] = []
    for s in symbols:
        cls = classify_symbol(s)
        if cls == "future":
            future.append(s)
        elif cls == "equity":
            equity.append(s)
        else:  # crypto / option — deferred, no REST bar endpoint in our adapters
            unsupported.append(s)
    return equity, future, unsupported


class HybridMarketData:
    """MarketDataProvider that routes each symbol to Alpaca (equity/crypto/option)
    or Schwab (futures)."""

    def __init__(
        self,
        alpaca: MarketDataProvider | None = None,
        schwab: MarketDataProvider | None = None,
    ) -> None:
        # Injectable for tests; lazily resolved on the live path.
        self._alpaca = alpaca
        self._schwab = schwab

    # -- child accessors -------------------------------------------------
    @property
    def alpaca(self) -> MarketDataProvider:
        if self._alpaca is None:
            from brokers.factory import get_market_data_provider

            self._alpaca = get_market_data_provider("alpaca")
        return self._alpaca

    @property
    def schwab(self) -> MarketDataProvider:
        if self._schwab is None:
            from brokers.factory import get_market_data_provider

            self._schwab = get_market_data_provider("schwab")
        return self._schwab

    def _child_for(self, symbol: str) -> MarketDataProvider:
        return self.schwab if classify_symbol(symbol) == "future" else self.alpaca

    # -- MarketDataProvider protocol ------------------------------------
    async def aget_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        days: int = 180,
        extended_hours: bool = False,
    ) -> list[StockBar]:
        cls = classify_symbol(symbol)
        if cls in ("crypto", "option"):
            logger.warning(
                "HybridMarketData: no REST bar endpoint for %s (%s) — returning empty",
                symbol,
                cls,
            )
            return []
        return await self._child_for(symbol).aget_stock_bars(
            symbol, timeframe=timeframe, days=days, extended_hours=extended_hours
        )

    async def aget_stock_bars_range(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        start: datetime,
        end: datetime,
    ) -> list[StockBar]:
        cls = classify_symbol(symbol)
        if cls in ("crypto", "option"):
            logger.warning(
                "HybridMarketData: no REST bar endpoint for %s (%s) — returning empty",
                symbol,
                cls,
            )
            return []
        return await self._child_for(symbol).aget_stock_bars_range(
            symbol, timeframe=timeframe, start=start, end=end
        )

    async def aget_stock_bars_batch(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1Day",
        days: int = 12,
    ) -> dict[str, list[StockBar]]:
        equity_syms, future_syms, unsupported = _route(list(symbols))
        if unsupported:
            logger.warning(
                "HybridMarketData: no REST bar endpoint for %s — skipping "
                "(crypto/option deferred)",
                unsupported,
            )
        merged: dict[str, list[StockBar]] = {}
        # Fault-isolate each leg: a Schwab (futures) failure must NOT discard the
        # already-fetched Alpaca equity bars — that would 502 the whole watchlist and
        # retry-storm (nothing caches on a failed batch). Serve whatever succeeds.
        if equity_syms:
            try:
                merged.update(
                    await self.alpaca.aget_stock_bars_batch(
                        equity_syms, timeframe=timeframe, days=days
                    )
                )
            except Exception:
                logger.warning(
                    "HybridMarketData: Alpaca batch bars failed for %d symbols; "
                    "serving partial",
                    len(equity_syms),
                    exc_info=True,
                )
        if future_syms:
            try:
                merged.update(
                    await self.schwab.aget_stock_bars_batch(
                        future_syms, timeframe=timeframe, days=days
                    )
                )
            except Exception:
                logger.warning(
                    "HybridMarketData: Schwab batch bars failed for %d futures; "
                    "serving partial",
                    len(future_syms),
                    exc_info=True,
                )
        return merged

    async def aget_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Extended-hours + fundamentals quote batch (Schwab-only surface).

        Only Alpaca-less data (pre/post last, PE/EPS/div/shares/earnings) lives here
        and only Schwab exposes it, so the equity slice routes to the Schwab child;
        futures (and deferred crypto/option) carry no ext/fundamental enrichment and
        are dropped. This is a single batched call per poll cycle — not the per-symbol
        bar storm — so it doesn't re-flood the WAF, and SIP's native pre/post prices
        already make it redundant for equities (the apply_extended wiring is left
        intact this round to limit risk; see the brief).
        """
        equity_syms, _futures, _unsupported = _route(list(symbols))
        if not equity_syms:
            return {}
        return await self.schwab.aget_quotes(equity_syms)
