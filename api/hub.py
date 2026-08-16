"""The ONE live market-data hub (WS quotes to the cockpit), built once at import.

Follows the DATA provider (MARKET_DATA_PROVIDER), never the execution broker, and
binds ``brokers.market_stream.market_hub`` to itself so the trading path
(execution_service / fill_tracker / exit_engine / proposals) broadcasts on the
same hub. ``api/main`` re-exports ``market_hub`` for the routes + lifespan; the
job modules and ``api/watchlist_routes`` import it from here.
"""

from __future__ import annotations

from brokers import market_stream as _market_stream
from brokers.factory import market_data_provider_name
from brokers.market_stream import AlpacaMarketStreamHub, BaseMarketHub
from brokers.schwab_market_stream import SchwabMarketStreamHub


# NB: the live-quote hub follows the DATA provider (MARKET_DATA_PROVIDER), NOT the
# execution broker. Keeps Schwab data/streaming decoupled from order routing —
# Schwab stays read-only; activating Schwab streaming must never imply Schwab execution.
def _build_market_hub() -> BaseMarketHub:
    provider = market_data_provider_name()
    if provider == "hybrid":
        from brokers.hybrid_market_stream import HybridMarketStreamHub

        return HybridMarketStreamHub()
    if provider == "schwab":
        return SchwabMarketStreamHub()
    return AlpacaMarketStreamHub()


market_hub: BaseMarketHub = _build_market_hub()

# The trading path (execution_service/fill_tracker/exit_engine/proposals) imports
# `brokers.market_stream.market_hub` to broadcast alerts. Point that alias at THIS live hub
# so execution/fill/exit alerts reach connected WS clients (cockpit notifications).
_market_stream.market_hub.bind(market_hub)
