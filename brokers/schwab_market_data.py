"""Schwab read-only market data via schwab-py price history."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from schwab.client import Client

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from brokers.schwab import SchwabError
from brokers.schwab_auth import create_client
from brokers.symbol_class import classify_symbol

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

_BATCH_CONCURRENCY = 6

# --- Akamai WAF circuit breaker -------------------------------------------------
# Schwab sits behind an Akamai WAF that rate-blocks the server IP with a 403 (or
# 429) burst when we request too hard — and once blocked, every chart refetch and
# the bars prewarm loop keep hammering the blocked endpoint with ZERO backoff,
# which SUSTAINS the block (self-inflicted outage, 2026-07-07: a deploy cold-start
# burst tripped it and it never recovered on its own). The breaker converts that
# storm into one probe per cooldown: the first 403/429 opens it, and while open we
# raise immediately WITHOUT calling Schwab (callers fall back to Alpaca / cache),
# letting the WAF cool. Module-level = shared across every instance + both the
# quotes and pricehistory paths (the block is IP-level, not per-endpoint).
_WAF_COOLDOWN_S = 90.0
_WAF_STATUS = frozenset({403, 429})
_waf_blocked_until = 0.0

# Global min-spacing between Schwab requests (the 07-22 outage follow-up). The
# breaker alone converts a storm into one probe per cooldown — but the moment it
# CLOSES, every queued fetch (charts + prewarm + quotes) fires concurrently, and
# that burst trips Akamai again: a self-sustaining 429→cooldown→herd cycle
# (observed live 07-23: ~90–120s clusters for four hours straight). Serializing
# requests at 4/s stays far under Schwab's per-minute budget while adding at
# most ~250ms latency per call; both call paths run in executor threads, so the
# sleep never touches the event loop.
_MIN_SPACING_S = 0.25
_pace_lock = threading.Lock()
_last_request_mono = 0.0


def _waf_gate() -> None:
    """Raise if the WAF breaker is open; otherwise pace the request globally."""
    import time as _time

    remaining = _waf_blocked_until - _time.monotonic()
    if remaining > 0:
        raise BrokerError(
            f"Schwab WAF cooldown active ({remaining:.0f}s left) — skipping request"
        )
    global _last_request_mono
    # Holding the lock through the sleep is deliberate: concurrent callers queue
    # and each departs _MIN_SPACING_S after the previous one — no burst survives.
    with _pace_lock:
        wait = _MIN_SPACING_S - (_time.monotonic() - _last_request_mono)
        if wait > 0:
            _time.sleep(wait)
        _last_request_mono = _time.monotonic()


def _waf_check(response: Any) -> None:
    """Open the breaker on a WAF status, then defer to raise_for_status().

    Called in place of a bare ``response.raise_for_status()`` so a 403/429 both
    arms the cooldown AND raises (→ Alpaca/cache fallback) in one step.
    """
    import time as _time

    global _waf_blocked_until
    if getattr(response, "status_code", None) in _WAF_STATUS:
        _waf_blocked_until = _time.monotonic() + _WAF_COOLDOWN_S
        logger.warning(
            "Schwab WAF %s — circuit breaker OPEN for %.0fs (serving fallback)",
            response.status_code,
            _WAF_COOLDOWN_S,
        )
    response.raise_for_status()

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
        if timeframe in ("1Week", "1Month"):
            return {
                "period_type": Client.PriceHistory.PeriodType.YEAR,
                "frequency_type": Client.PriceHistory.FrequencyType.DAILY,
                "frequency": Client.PriceHistory.Frequency.DAILY,
            }
        if timeframe == "4Hour":
            return {
                "period_type": Client.PriceHistory.PeriodType.DAY,
                "frequency_type": Client.PriceHistory.FrequencyType.MINUTE,
                "frequency": Client.PriceHistory.Frequency.EVERY_THIRTY_MINUTES,
            }
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
        # Schwab can return duplicate candles for the same timestamp (notably with
        # extended hours spanning day boundaries). Collapse to one bar per timestamp
        # (last wins), then sort — duplicates break charts (lightweight-charts needs
        # unique, strictly-ascending times → renders nothing) and would double-count
        # volume in RVOL / volume profiles.
        deduped = {bar.time: bar for bar in bars}
        return sorted(deduped.values(), key=lambda bar: bar.time)

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

    @staticmethod
    def _globex_bucket_epoch(dt: datetime, bucket_s: int) -> int:
        """Epoch-second start of *dt*'s bucket, anchored on the 18:00 ET Globex open.

        CME sessions run 18:00 ET (D-1) -> 17:00 ET (D), so the last bucket of a
        session is a short stub (14:00-17:00 ET for 4H) and the next session
        restarts the grid at 18:00 — exactly how ToS draws it. No session straddles
        a US DST switch (both transitions land Sunday 02:00 ET, before the Sunday
        18:00 open), so wall-clock anchoring needs no fold handling.
        """
        et = dt.astimezone(_ET)
        anchor = et.replace(hour=18, minute=0, second=0, microsecond=0)
        if et < anchor:
            anchor -= timedelta(days=1)
        offset = int((et - anchor).total_seconds()) // bucket_s * bucket_s
        return int(anchor.timestamp()) + offset

    @classmethod
    def _aggregate_to_4hourly(
        cls, bars: list[StockBar], *, session_anchored: bool = False
    ) -> list[StockBar]:
        """Fold 30-minute bars into 4-hour bars.

        ``session_anchored`` (futures) buckets from the 18:00 ET Globex open;
        otherwise buckets from UTC midnight. Futures NEED the session anchor:
        18:00 ET is 22:00/23:00 UTC, neither a multiple of 4h, so UTC-epoch
        buckets sat 2h off ToS and slid by an hour at every DST change (measured
        on /ES: 12 distinct bucket starts across 180 days instead of 6). Worse,
        the 16:00 ET bucket swallowed the 17:00-18:00 maintenance break AND the
        next session's open, hiding the session boundary inside a candle. Every
        shorter timeframe is safe because 18:00 ET is an exact multiple of
        1/5/15/30/60 minutes from UTC midnight — 4H is the only one that isn't.
        """
        if not bars:
            return []

        bucket_s = 4 * 3600
        buckets: dict[int, list[StockBar]] = defaultdict(list)
        for bar in bars:
            dt = datetime.fromisoformat(bar.time.replace("Z", "+00:00"))
            key = (
                cls._globex_bucket_epoch(dt, bucket_s)
                if session_anchored
                else int(dt.timestamp()) // bucket_s * bucket_s
            )
            buckets[key].append(bar)

        four_hourly: list[StockBar] = []
        for key in sorted(buckets):
            group = buckets[key]
            bucket_start = datetime.fromtimestamp(key, tz=UTC)
            four_hourly.append(
                StockBar(
                    time=bucket_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    open=group[0].open,
                    high=max(b.high for b in group),
                    low=min(b.low for b in group),
                    close=group[-1].close,
                    volume=sum(b.volume for b in group),
                )
            )
        return four_hourly

    @staticmethod
    def _aggregate_to_weekly(bars: list[StockBar]) -> list[StockBar]:
        if not bars:
            return []

        buckets: dict[tuple[int, int], list[StockBar]] = defaultdict(list)
        for bar in bars:
            dt = datetime.fromisoformat(bar.time.replace("Z", "+00:00"))
            iso_year, iso_week, _ = dt.isocalendar()
            buckets[(iso_year, iso_week)].append(bar)

        weekly: list[StockBar] = []
        for bucket_key in sorted(buckets):
            group = buckets[bucket_key]
            weekly.append(
                StockBar(
                    time=group[0].time,
                    open=group[0].open,
                    high=max(b.high for b in group),
                    low=min(b.low for b in group),
                    close=group[-1].close,
                    volume=sum(b.volume for b in group),
                )
            )
        return weekly

    @staticmethod
    def _aggregate_to_monthly(bars: list[StockBar]) -> list[StockBar]:
        if not bars:
            return []

        buckets: dict[tuple[int, int], list[StockBar]] = defaultdict(list)
        for bar in bars:
            dt = datetime.fromisoformat(bar.time.replace("Z", "+00:00"))
            buckets[(dt.year, dt.month)].append(bar)

        monthly: list[StockBar] = []
        for bucket_key in sorted(buckets):
            group = buckets[bucket_key]
            monthly.append(
                StockBar(
                    time=group[0].time,
                    open=group[0].open,
                    high=max(b.high for b in group),
                    low=min(b.low for b in group),
                    close=group[-1].close,
                    volume=sum(b.volume for b in group),
                )
            )
        return monthly

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

        _waf_gate()
        params = self._resolve_timeframe(timeframe)
        response = self._client.get_price_history(
            symbol,
            start_datetime=start.astimezone(UTC),
            end_datetime=end.astimezone(UTC),
            need_extended_hours_data=extended_hours,
            **params,
        )
        _waf_check(response)
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
        if timeframe == "4Hour":
            bars = await asyncio.to_thread(
                self._fetch_bars_sync,
                symbol,
                timeframe="30Min",
                start=start,
                end=end,
                extended_hours=extended_hours,
            )
            return self._aggregate_to_4hourly(
                bars, session_anchored=classify_symbol(symbol) == "future"
            )
        if timeframe == "1Week":
            bars = await asyncio.to_thread(
                self._fetch_bars_sync,
                symbol,
                timeframe="1Day",
                start=start,
                end=end,
                extended_hours=extended_hours,
            )
            return self._aggregate_to_weekly(bars)
        if timeframe == "1Month":
            bars = await asyncio.to_thread(
                self._fetch_bars_sync,
                symbol,
                timeframe="1Day",
                start=start,
                end=end,
                extended_hours=extended_hours,
            )
            return self._aggregate_to_monthly(bars)
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
        _waf_gate()
        response = self._client.get_quotes(symbols)
        _waf_check(response)
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
            prev_close = _f(prev_close_raw)
            post_pct = _f(post_pct_raw)

            # Extended/overnight last trade: take whichever of the consolidated `quote` block
            # or the `extended` block has the more RECENT tradeTime. Schwab keeps one live and
            # freezes the other depending on session — weekday post-market the `quote` block is
            # live while `extended` goes stale (TSLA: extended 15h old, quote fresh); weekend/
            # overnight the reverse (SPY Sun-night: quote frozen at Friday, extended live 733.3).
            # Freshest-by-time is correct for both; fall back to any value if a tradeTime is absent.
            # A block only counts as a live price if it actually has a market
            # (non-zero bid/ask or volume). Schwab leaves a STALE `extended` block
            # — bid/ask/volume all 0 — carrying a misleadingly recent tradeTime
            # during weekday pre/post-market; trusting its tradeTime fed a phantom
            # price (e.g. an old 720 while the real last is 737) into the watchlist
            # AND price alerts. Require liveness so the genuinely-traded block wins.
            _e_live = bool(
                _f(extended.get("bidPrice"))
                or _f(extended.get("askPrice"))
                or _f(extended.get("totalVolume"))
            )
            _q_last, _q_time = _f(quote.get("lastPrice")), _f(quote.get("tradeTime"))
            _e_last = _f(extended.get("lastPrice")) or _f(extended.get("mark"))
            _e_time = _f(extended.get("tradeTime"))
            _cands = []
            # A quote-block lastPrice that merely ECHOES the regular close carries
            # no ext information — pre-market Schwab keeps bumping quote.tradeTime
            # while lastPrice stays frozen at yesterday's close, so the stale close
            # won the timestamp race against live extended trades. The REST poll
            # then ping-ponged state.ext_price against the stream's real ext ticks
            # (each disagreement = a phantom alert "cross": 195.55 vs ~191 on NVDA,
            # 2026-07-07 pre-market spam). Only exclude the echo when a LIVE
            # extended candidate exists — with no ext market at all, echoing the
            # close is stable and harmless.
            _q_echoes_close = (
                _q_last is not None and reg_last is not None and _q_last == reg_last
            )
            _e_cand_ok = _e_last is not None and _e_time is not None and _e_live
            if (
                _q_last is not None
                and _q_time is not None
                and not (_q_echoes_close and _e_cand_ok)
            ):
                _cands.append((_q_time, _q_last))
            if _e_cand_ok:
                _cands.append((_e_time, _e_last))
            if _cands:
                ext_last = max(_cands, key=lambda c: c[0])[1]
            else:
                ext_last = _q_last if _q_last is not None else _e_last

            # Compute the move vs today's regular close FIRST so the % always agrees with the
            # freshest ext_last above (Schwab's postMarketPercentChange can be stale overnight).
            # This equals postMarketPercentChange in the normal weekday post-market case.
            ext_change_pct: float | None = None
            if ext_last is not None and reg_last and reg_last != 0:
                ext_change_pct = round((ext_last - reg_last) / reg_last * 100, 2)
            elif post_pct is not None:
                ext_change_pct = round(post_pct, 2)
            elif ext_last is not None and prev_close and prev_close != 0:
                ext_change_pct = round((ext_last - prev_close) / prev_close * 100, 2)

            total_volume = _f(quote.get("totalVolume"))

            fundamental = data.get("fundamental") or {}
            pe_ratio = _f(fundamental.get("peRatio"))
            eps = _f(fundamental.get("eps"))
            div_yield = _f(fundamental.get("divYield"))
            shares_outstanding = _f(fundamental.get("sharesOutstanding"))

            # lastEarningsDate is deliberately NOT parsed: past announce dates come from
            # SEC EDGAR (free, keyless), so no capability is gated on holding a Schwab
            # account. See signals/earnings_edgar.py.

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
            }
        return out

    async def aget_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Batch quote fetch; returns {SYMBOL: {regular_last, regular_change_pct,
        ext_last, ext_change_pct, prev_close, total_volume, pe_ratio, eps, div_yield,
        shares_outstanding}} with None for absent fields.

        total_volume is the cumulative day volume from Schwab's quote.totalVolume field.
        Useful for efficient market-wide RVOL screening without per-symbol bar calls.

        Fundamental fields (pe_ratio, eps, div_yield, shares_outstanding) come from
        Schwab's fundamental block and may be None for symbols without that data
        (e.g. ETFs). Earnings dates are NOT sourced here — see signals/earnings_edgar.py.

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
