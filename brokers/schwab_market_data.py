"""Schwab read-only market data via schwab-py price history."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
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

    @staticmethod
    def _aggregate_to_hourly(bars: list[StockBar]) -> list[StockBar]:
        if not bars:
            return []

        buckets: dict[str, list[StockBar]] = defaultdict(list)
        for bar in bars:
            dt = datetime.fromisoformat(bar.time.replace("Z", "+00:00"))
            hour_dt = dt.replace(minute=0, second=0, microsecond=0)
            hour_key = hour_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            buckets[hour_key].append(bar)

        hourly: list[StockBar] = []
        for hour_key in sorted(buckets):
            group = buckets[hour_key]
            hourly.append(
                StockBar(
                    time=hour_key,
                    open=group[0].open,
                    high=max(b.high for b in group),
                    low=min(b.low for b in group),
                    close=group[-1].close,
                    volume=sum(b.volume for b in group),
                )
            )
        return hourly

    def _fetch_bars_sync(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        extended_hours: bool = False,
    ) -> list[StockBar]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise BrokerError("Symbol is required")

        params = self._resolve_timeframe(timeframe)
        response = self._client.get_price_history(
            symbol,
            start_datetime=start.astimezone(UTC),
            end_datetime=end.astimezone(UTC),
            need_extended_hours_data=extended_hours,
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
        extended_hours: bool = False,
    ) -> list[StockBar]:
        if timeframe == "1Hour":
            bars = await asyncio.to_thread(
                self._fetch_bars_sync,
                symbol,
                timeframe="30Min",
                start=start,
                end=end,
                extended_hours=extended_hours,
            )
            return self._aggregate_to_hourly(bars)
        return await asyncio.to_thread(
            self._fetch_bars_sync,
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            extended_hours=extended_hours,
        )

    async def aget_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        days: int = 180,
        extended_hours: bool = False,
    ) -> list[StockBar]:
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        return await self.aget_stock_bars_range(
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            extended_hours=extended_hours,
        )

    async def _fetch_symbol_batch(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        semaphore: asyncio.Semaphore,
        extended_hours: bool = False,
    ) -> tuple[str, list[StockBar] | None]:
        async with semaphore:
            try:
                bars = await self.aget_stock_bars_range(
                    symbol,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    extended_hours=extended_hours,
                )
            except Exception as exc:
                logger.warning(
                    "Schwab batch bar fetch failed for %s: %s", symbol, exc
                )
                return symbol, None
            return symbol, bars

    def _fetch_quotes_sync(self, symbols: list[str]) -> dict[str, dict]:
        """Sync inner: call get_quotes, parse regular/extended/quote blocks."""
        response = self._client.get_quotes(symbols)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise SchwabError(f"Unexpected get_quotes response type: {type(payload)}")

        out: dict[str, dict] = {}
        for sym, data in payload.items():
            if not isinstance(data, dict):
                continue
            regular = data.get("regular") or {}
            extended = data.get("extended") or {}
            quote = data.get("quote") or {}

            # Regular-session price: prefer regularMarketLastPrice, fall back to lastPrice
            reg_last_raw = regular.get("regularMarketLastPrice") or quote.get("lastPrice")
            reg_change_pct_raw = regular.get("regularMarketPercentChange") or quote.get(
                "netPercentChangeInDouble"
            )
            # Extended-hours price: extended block's lastPrice (or mark as fallback)
            ext_last_raw = extended.get("lastPrice") or extended.get("mark")
            prev_close_raw = quote.get("closePrice")
            # Schwab's own post/overnight % move (vs today's regular close, like ToS)
            post_pct_raw = quote.get("postMarketPercentChange")

            def _f(v: object) -> float | None:
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            reg_last = _f(reg_last_raw)
            reg_change_pct = _f(reg_change_pct_raw)
            ext_last = _f(ext_last_raw)
            prev_close = _f(prev_close_raw)
            post_pct = _f(post_pct_raw)

            # Extended/overnight % move: prefer Schwab's own postMarketPercentChange
            # (vs today's regular close, matching thinkorswim); else compute vs today's
            # regular close (correct for pre/post/overnight); else vs prior-day close.
            ext_change_pct: float | None = None
            if post_pct is not None:
                ext_change_pct = round(post_pct, 2)
            elif ext_last is not None and reg_last and reg_last != 0:
                ext_change_pct = round((ext_last - reg_last) / reg_last * 100, 2)
            elif ext_last is not None and prev_close and prev_close != 0:
                ext_change_pct = round((ext_last - prev_close) / prev_close * 100, 2)

            total_volume = _f(quote.get("totalVolume"))

            fundamental = data.get("fundamental") or {}
            pe_ratio = _f(fundamental.get("peRatio"))
            eps = _f(fundamental.get("eps"))
            div_yield = _f(fundamental.get("divYield"))
            shares_outstanding = _f(fundamental.get("sharesOutstanding"))

            # Schwab returns lastEarningsDate as an ISO string ("2026-04-30T00:00:00Z"),
            # NOT epoch-ms — take the date part. (Schwab gives no nextEarningsDate.)
            last_earnings_raw = fundamental.get("lastEarningsDate")
            last_earnings_date: str | None = None
            if isinstance(last_earnings_raw, str) and len(last_earnings_raw) >= 10:
                last_earnings_date = last_earnings_raw[:10]
            elif last_earnings_raw is not None:
                try:
                    last_earnings_date = datetime.fromtimestamp(
                        int(last_earnings_raw) / 1000, tz=UTC
                    ).strftime("%Y-%m-%d")
                except (TypeError, ValueError, OSError):
                    pass

            out[sym.upper()] = {
                "regular_last": reg_last,
                "regular_change_pct": _f(reg_change_pct),
                "ext_last": ext_last,
                "ext_change_pct": ext_change_pct,
                "prev_close": prev_close,
                "total_volume": total_volume,
                "pe_ratio": pe_ratio,
                "eps": eps,
                "div_yield": div_yield,
                "shares_outstanding": shares_outstanding,
                "last_earnings_date": last_earnings_date,
            }
        return out

    async def aget_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Batch quote fetch; returns {SYMBOL: {regular_last, regular_change_pct,
        ext_last, ext_change_pct, prev_close, total_volume, pe_ratio, eps, div_yield,
        shares_outstanding, last_earnings_date}} with None for absent fields.

        total_volume is the cumulative day volume from Schwab's quote.totalVolume field.
        Useful for efficient market-wide RVOL screening without per-symbol bar calls.

        Fundamental fields (pe_ratio, eps, div_yield, shares_outstanding,
        last_earnings_date) come from Schwab's fundamental block and may be None for
        symbols without that data (e.g. ETFs).

        Raises BrokerError on hard failure (non-200 or unexpected response shape).
        """
        cleaned = [s.strip().upper() for s in symbols if s.strip()]
        if not cleaned:
            return {}
        try:
            return await asyncio.to_thread(self._fetch_quotes_sync, cleaned)
        except (BrokerError, SchwabError):
            raise
        except Exception as exc:
            raise BrokerError(f"Schwab get_quotes failed: {exc}") from exc

    async def aget_stock_bars_batch(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1Day",
        days: int = 12,
        extended_hours: bool = False,
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
                    extended_hours=extended_hours,
                )
                for symbol in cleaned
            )
        )

        out: dict[str, list[StockBar]] = {}
        for symbol, bars in results:
            if bars is not None:
                out[symbol] = bars
        return out
