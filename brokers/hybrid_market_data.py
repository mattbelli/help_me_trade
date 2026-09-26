"""Per-asset-class REST market-data router (Seam A).

`HybridMarketData` implements the `MarketDataProvider` protocol by delegating each
symbol to a child provider chosen by `classify_symbol`: futures → Schwab, equities/ETFs
→ Alpaca SIP. Batch methods PARTITION the symbol list, call each child with only its
slice, and MERGE the results — so a mixed watchlist fans out to Alpaca (the ~480
req/min bar storm that trips Schwab's Akamai WAF) and leaves only the handful of
futures on Schwab. Each child leg is fault-isolated: if one provider fails, the other's
bars are still served (a Schwab hiccup must never black out the equity watchlist).

Crypto (BTC/USD…) routes to Alpaca too, but through its OWN v1beta3 endpoints
(`aget_crypto_bars_*`) — the stock endpoints cannot serve a slashed pair. Options
still have no REST bar endpoint in our adapters (deferred), so they are surfaced as
an explicit gap — logged + skipped — rather than silently forwarded to the stock
endpoint (which would return quietly empty data).

Selected via `MARKET_DATA_PROVIDER=hybrid` (see brokers.factory). Deny-by-default:
the factory refuses to build this unless Alpaca is configured, so equities can never
silently fall back to Schwab and re-flood the WAF.

Children are built lazily through `get_market_data_provider` so its Schwab
token-mtime cache-heal still applies, and the Schwab child is only constructed when a
future is actually routed (a Schwab-less setup still serves equities).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from brokers.base import MarketDataProvider
from brokers.market_clock import market_session
from brokers.symbol_class import SCHWAB_CLASSES, classify_symbol
from trading.market_hours import last_session_close

if TYPE_CHECKING:
    from brokers.alpaca import StockBar

logger = logging.getLogger(__name__)

# Schwab-only fields merged on top of Alpaca ext prices (fail-soft — Schwab outage
# must never blank ext_last/ext_change_pct).
_SCHWAB_SUPPLEMENT_KEYS = (
    "regular_last",
    "regular_change_pct",
    "prev_close",
    "total_volume",
    "pe_ratio",
    "eps",
    "div_yield",
    "shares_outstanding",
)


def _overnight_quote_feed(adapter: object) -> str:
    from brokers.alpaca_tier import data_tier

    return "boats" if data_tier(adapter) == "paid" else "overnight"


def _overnight_delayed(adapter: object) -> bool:
    from brokers.alpaca_tier import data_tier

    return market_session() == "overnight" and data_tier(adapter) == "free"


def _route(
    symbols: list[str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split *symbols* into (equity, future, crypto, unsupported), order-preserving.

    equity/ETF → Alpaca stocks, future → Schwab, crypto → Alpaca's crypto
    endpoints. Options have no REST bar endpoint yet, so they land in
    `unsupported` and are surfaced as a visible gap rather than silently routed
    to the wrong (stock) endpoint.
    """
    equity: list[str] = []
    future: list[str] = []
    crypto: list[str] = []
    unsupported: list[str] = []
    for s in symbols:
        cls = classify_symbol(s)
        if cls in SCHWAB_CLASSES:
            future.append(s)
        elif cls == "equity":
            equity.append(s)
        elif cls == "crypto":
            crypto.append(s)
        else:  # option — deferred, no REST bar endpoint in our adapters
            unsupported.append(s)
    return equity, future, crypto, unsupported


class HybridMarketData:
    """MarketDataProvider that routes each symbol to Alpaca (equity/crypto)
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
        return self.schwab if classify_symbol(symbol) in SCHWAB_CLASSES else self.alpaca

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
        if cls == "option":
            logger.warning(
                "HybridMarketData: no REST bar endpoint for %s (option) — returning empty",
                symbol,
            )
            return []
        if cls == "crypto":
            # extended_hours is meaningless for a 24/7 market — ignored.
            end = datetime.now(UTC)
            return await self.alpaca.aget_crypto_bars_range(
                symbol,
                timeframe=timeframe,
                start=end - timedelta(days=days),
                end=end,
            )
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
        extended_hours: bool = False,
    ) -> list[StockBar]:
        cls = classify_symbol(symbol)
        if cls == "option":
            logger.warning(
                "HybridMarketData: no REST bar endpoint for %s (option) — returning empty",
                symbol,
            )
            return []
        if cls == "crypto":
            return await self.alpaca.aget_crypto_bars_range(
                symbol, timeframe=timeframe, start=start, end=end
            )
        child = self._child_for(symbol)
        if cls == "equity" and hasattr(child, "aget_stock_bars_range"):
            return await child.aget_stock_bars_range(
                symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                extended_hours=extended_hours,
            )
        return await child.aget_stock_bars_range(
            symbol, timeframe=timeframe, start=start, end=end
        )

    async def aget_stock_bars_latest(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        limit: int = 300,
        before: datetime | None = None,
        extended_hours: bool = False,
    ) -> tuple[list[StockBar], bool]:
        cls = classify_symbol(symbol)
        if cls == "option":
            logger.warning(
                "HybridMarketData: no REST bar endpoint for %s (option) — returning empty",
                symbol,
            )
            return [], False
        if cls == "crypto":
            return await self.alpaca.aget_crypto_bars_latest(
                symbol, timeframe=timeframe, limit=limit, before=before
            )
        return await self._child_for(symbol).aget_stock_bars_latest(
            symbol,
            timeframe=timeframe,
            limit=limit,
            before=before,
            extended_hours=extended_hours,
        )

    async def aget_stock_bars_batch(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1Day",
        days: int = 12,
    ) -> dict[str, list[StockBar]]:
        equity_syms, future_syms, crypto_syms, unsupported = _route(list(symbols))
        if unsupported:
            logger.warning(
                "HybridMarketData: no REST bar endpoint for %s — skipping "
                "(options deferred)",
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
        if crypto_syms:
            try:
                merged.update(
                    await self.alpaca.aget_crypto_bars_batch(
                        crypto_syms, timeframe=timeframe, days=days
                    )
                )
            except Exception:
                logger.warning(
                    "HybridMarketData: Alpaca crypto batch bars failed for %d pairs; "
                    "serving partial",
                    len(crypto_syms),
                    exc_info=True,
                )
        return merged

    async def aget_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Extended-hours quotes for equities (Alpaca) + optional Schwab enrichments.

        Equity pre/post/overnight last price routes to Alpaca snapshots so a Schwab
        outage never blanks equity charts. During the overnight session (8pm–4am ET)
        the quote feed is tier-aware: ``boats`` (paid, real-time) or ``overnight``
        (free, indicative). Futures, crypto (24/7 — there is no ext session; its
        live price comes from the stream) and deferred options carry no ext
        enrichment and are dropped. Schwab-only fields (fundamentals, total_volume,
        regular_last) are fetched in a separate fail-soft leg — a Schwab failure must
        NOT break the Alpaca ext-price path.
        """
        equity_syms, _futures, _crypto, _unsupported = _route(list(symbols))
        if not equity_syms:
            return {}

        session = market_session()
        quote_feed: str | None = None
        not_before: datetime | None = None
        if session == "overnight":
            quote_feed = _overnight_quote_feed(self.alpaca)
            # The overnight venue trades few names. Its snapshot still reports the
            # last trade it ever saw, so a symbol with no overnight market serves a
            # print from a PRIOR session as if it were live (NTSK: a 20.6h-old
            # 100-share print rendered -10% on a stock that closed +11%). Anything
            # older than the last regular close was superseded by that close.
            not_before = last_session_close(datetime.now(UTC))
        delayed = _overnight_delayed(self.alpaca)

        merged: dict[str, dict] = {}
        try:
            ext = await self.alpaca.aget_ext_quotes(
                equity_syms, feed=quote_feed, not_before=not_before
            )
            if session == "overnight" and quote_feed is not None:
                # Per-symbol, not all-or-nothing: on a mixed watchlist the actively
                # traded names have fresh overnight prints while the rest must fall
                # back to the consolidated feed's last real trade.
                missing = [s for s in equity_syms if s not in ext]
                if missing:
                    ext.update(await self.alpaca.aget_ext_quotes(missing))
            merged.update(ext)
        except Exception:
            logger.warning(
                "HybridMarketData: Alpaca ext quotes failed for %d symbols",
                len(equity_syms),
                exc_info=True,
            )

        if delayed:
            for sym in equity_syms:
                bucket = merged.setdefault(sym, {})
                bucket["overnight_delayed"] = True

        try:
            schwab_quotes = await self.schwab.aget_quotes(equity_syms)
        except Exception:
            logger.warning(
                "HybridMarketData: Schwab supplemental quotes failed for %d symbols",
                len(equity_syms),
                exc_info=True,
            )
        else:
            for sym, sq in schwab_quotes.items():
                bucket = merged.setdefault(sym, {})
                for key in _SCHWAB_SUPPLEMENT_KEYS:
                    val = sq.get(key)
                    if val is not None:
                        bucket[key] = val

        # Overnight ext-% fix: the boats snapshot's dailyBar IS the overnight session
        # (its close ≈ the latest trade), so aget_ext_quotes computes ext_change_pct ≈ 0.
        # Recompute the overnight move against the real regular close (regular_last =
        # today's RTH close; fallback prev_close) from the Schwab supplement — consistent
        # with how post-market is referenced.
        if session == "overnight":
            for bucket in merged.values():
                ext_last = bucket.get("ext_last")
                ref = bucket.get("regular_last") or bucket.get("prev_close")
                if ext_last is None or not ref:
                    continue
                try:
                    bucket["ext_change_pct"] = round(
                        (float(ext_last) - float(ref)) / float(ref) * 100.0, 2
                    )
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

        return merged
