"""Schwab read-only market data via schwab-py price history."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from schwab.client import Client

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from brokers.schwab import SchwabError
from brokers.schwab_auth import create_client

logger = logging.getLogger(__name__)

_BATCH_CONCURRENCY = 6

_TIMEFRAME_MINUTE: dict[str, Client.PriceHistory.Frequency] = {
    "1Min": Client.PriceHistory.Frequency.EVERY_MINUTE,
    "5Min": Client.PriceHistory.Frequency.EVERY_FIVE_MINUTES,
    "15Min": Client.PriceHistory.Frequency.EVERY_FIFTEEN_MINUTES,
    "30Min": Client.PriceHistory.Frequency.EVERY_THIRTY_MINUTES,
}


class SchwabMarketData:
    """Read-only Schwab market data — no order or execution methods."""

    def __init__(self, client: Client) -> None:
        self._client = client

    @classmethod
    def from_settings(cls) -> SchwabMarketData:
        try:
            client = create_client(interactive=False)
        except BrokerError:
            raise
        except Exception as exc:
            raise SchwabError(f"Failed to create Schwab client: {exc}") from exc
        return cls(client)

    @staticmethod
    def _resolve_timeframe(timeframe: str) -> dict[str, Any]:
        if timeframe == "1Day":
            return {
                "period_type": Client.PriceHistory.PeriodType.YEAR,
                "frequency_type": Client.PriceHistory.FrequencyType.DAILY,
                "frequency": Client.PriceHistory.Frequency.DAILY,
            }
        minute_freq = _TIMEFRAME_MINUTE.get(timeframe)
        if minute_freq is not None:
            return {
                "period_type": Client.PriceHistory.PeriodType.DAY,
                "frequency_type": Client.PriceHistory.FrequencyType.MINUTE,
                "frequency": minute_freq,
            }
        raise BrokerError(
            f"Schwab market data does not support timeframe {timeframe!r}"
        )

    @staticmethod
    def _epoch_ms_to_iso(epoch_ms: int) -> str:
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def _parse_candles(cls, payload: dict[str, Any]) -> list[StockBar]:
        if payload.get("empty"):
            return []

        raw_candles = payload.get("candles") or []
        bars: list[StockBar] = []
        for candle in raw_candles:
            bars.append(
                StockBar(
                    time=cls._epoch_ms_to_iso(int(candle["datetime"])),
                    open=float(candle["open"]),
                    high=float(candle["high"]),
                    low=float(candle["low"]),
                    close=float(candle["close"]),
                    volume=float(candle.get("volume", 0)),
                )
            )
        bars.sort(key=lambda bar: bar.time)
        return bars

    def _fetch_bars_sync(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[StockBar]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise BrokerError("Symbol is required")

        params = self._resolve_timeframe(timeframe)
        response = self._client.get_price_history(
            symbol,
            start_datetime=start.astimezone(UTC),
            end_datetime=end.astimezone(UTC),
            need_extended_hours_data=False,
            **params,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise SchwabError(f"Unexpected price history response for {symbol}")
        return self._parse_candles(payload)

    async def aget_stock_bars_range(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        start: datetime,
        end: datetime,
    ) -> list[StockBar]:
        return await asyncio.to_thread(
            self._fetch_bars_sync,
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )

    async def aget_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        days: int = 180,
    ) -> list[StockBar]:
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        return await self.aget_stock_bars_range(
            symbol, timeframe=timeframe, start=start, end=end
        )

    async def _fetch_symbol_batch(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, list[StockBar] | None]:
        async with semaphore:
            try:
                bars = await self.aget_stock_bars_range(
                    symbol,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                )
            except Exception as exc:
                logger.warning(
                    "Schwab batch bar fetch failed for %s: %s", symbol, exc
                )
                return symbol, None
            return symbol, bars

    async def aget_stock_bars_batch(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1Day",
        days: int = 12,
    ) -> dict[str, list[StockBar]]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            sym = raw.strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                cleaned.append(sym)

        if not cleaned:
            return {}

        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)
        results = await asyncio.gather(
            *(
                self._fetch_symbol_batch(
                    symbol,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    semaphore=semaphore,
                )
                for symbol in cleaned
            )
        )

        out: dict[str, list[StockBar]] = {}
        for symbol, bars in results:
            if bars is not None:
                out[symbol] = bars
        return out
