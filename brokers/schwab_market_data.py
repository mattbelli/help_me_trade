"""Schwab read-only market data via schwab-py price history."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from schwab.client import Client

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from brokers.schwab import SchwabError
from brokers.schwab_auth import create_client

logger = logging.getLogger(__name__)

# --- Global Schwab REST rate governor + WAF circuit breaker ---------------------
# Schwab sits behind an Akamai WAF that rate-blocks the SERVER IP (403/429) when we
# burst. The root cause of repeated outages: NO global budget — every subsystem
# (watchlist RVOL fan-out, chart /api/bars, the bars prewarm loop, the stream baseline
# seeder, quote polls) fired independently, so a deploy cold-start could push ~500-900
# pricehistory calls/min and trip the WAF; with no backoff the retry storm sustained
# the block. The durable fix is ONE module-level token bucket that ALL Schwab REST
# traffic passes through (the block is IP-level, so the budget is shared across every
# SchwabMarketData instance), with the WAF circuit breaker unified at the SAME
# chokepoint. This makes bursts impossible by construction — a cold-start prewarm of
# 40 symbols is naturally spread over ~10s instead of firing at once, so the boot
# warm-up ramp comes free from the bucket (no separate ramp is written).

# Rate numbers (named for easy tuning). Anchor: the watchlist TTL-cache fix ran stably
# at ~20 req/min (~0.33/s); the incident tripped at ~10-15/s. 4/s sustained keeps us
# well under the trip point while charts stay responsive; an 8-token burst absorbs a
# handful of simultaneous interactive requests without waiting.
_RATE_PER_SEC = 4.0          # sustained Schwab REST budget (IP-wide)
_RATE_BURST = 8.0            # bucket capacity (short burst allowance)
# Background callers (prewarm loop, baseline seeder) may draw a token only while the
# bucket still holds this many spare tokens for interactive requests — so background
# work spends only spare budget and can never starve a live user's chart/quote.
_BACKGROUND_RESERVE = 4.0
_MIN_WAIT_S = 0.01           # floor on the recheck sleep so we never busy-spin

# WAF circuit breaker (evolves the incident hotfix into a 3-state machine). The first
# 403/429 opens a cooldown during which every call fails fast WITHOUT hitting Schwab
# (callers fall back to Alpaca/cache); after the cooldown a SINGLE half-open probe is
# allowed — success closes it, another 403/429 re-opens. Module-level = shared across
# instances + both the quotes and pricehistory paths (the block is IP-level).
_WAF_COOLDOWN_S = 90.0
_WAF_STATUS = frozenset({403, 429})
_STATE_CLOSED = "closed"
_STATE_OPEN = "open"
_STATE_HALF_OPEN = "half_open"

# Clock indirection so tests can inject a fake monotonic clock (breaker + throttle log).
_monotonic = time.monotonic

_waf_state = _STATE_CLOSED
_waf_blocked_until = 0.0

# Observability (rate-limited so it can't spam the log).
_throttled_total = 0
_last_throttle_log = 0.0
_THROTTLE_LOG_INTERVAL_S = 30.0


def _note_throttled() -> None:
    """Count a paced (waited) acquire; emit a rate-limited INFO summary."""
    global _throttled_total, _last_throttle_log
    _throttled_total += 1
    now = _monotonic()
    if now - _last_throttle_log >= _THROTTLE_LOG_INTERVAL_S:
        _last_throttle_log = now
        logger.info(
            "Schwab rate governor throttled %d requests (pacing to %.0f req/s)",
            _throttled_total,
            _RATE_PER_SEC,
        )


def _waf_gate() -> bool:
    """Breaker gate, called at the START of a token acquire (before any token is spent).

    Returns True iff THIS call is the single half-open probe for the current cooldown.
    Raises BrokerError immediately — making ZERO Schwab calls — while the breaker is
    open or a probe is already in flight.
    """
    global _waf_state
    state = _waf_state
    if state == _STATE_CLOSED:
        return False
    if state == _STATE_HALF_OPEN:
        # A probe is already in flight for this cooldown — fail fast, don't pile on.
        raise BrokerError("Schwab WAF probe in flight — skipping request")
    # OPEN: fail fast until the cooldown elapses, then claim the one probe.
    remaining = _waf_blocked_until - _monotonic()
    if remaining > 0:
        raise BrokerError(
            f"Schwab WAF cooldown active ({remaining:.0f}s left) — skipping request"
        )
    _waf_state = _STATE_HALF_OPEN
    return True


def _waf_check(response: Any) -> None:
    """Inspect an HTTP response at the sync boundary, then defer to raise_for_status().

    A 403/429 opens the breaker and raises (→ Alpaca/cache fallback) in one step; any
    other status closes it — a half-open probe that got a real response has proven the
    WAF is done blocking us.
    """
    global _waf_state, _waf_blocked_until
    status = getattr(response, "status_code", None)
    if status in _WAF_STATUS:
        _waf_state = _STATE_OPEN
        _waf_blocked_until = _monotonic() + _WAF_COOLDOWN_S
        logger.warning(
            "Schwab WAF %s — circuit breaker OPEN for %.0fs (serving fallback)",
            status,
            _WAF_COOLDOWN_S,
        )
        response.raise_for_status()
        return
    if _waf_state != _STATE_CLOSED:
        _waf_state = _STATE_CLOSED
        logger.info("Schwab WAF probe succeeded — circuit breaker CLOSED")
    response.raise_for_status()


def _waf_probe_aborted() -> None:
    """A half-open probe raised WITHOUT a WAF verdict (e.g. a network error before any
    response) — stay cautious and re-open so we don't hammer a still-blocked WAF. No-op
    if the probe already resolved the state (closed on success, re-opened on 403/429).
    """
    global _waf_state, _waf_blocked_until
    if _waf_state == _STATE_HALF_OPEN:
        _waf_state = _STATE_OPEN
        _waf_blocked_until = _monotonic() + _WAF_COOLDOWN_S


class _RateLimiter:
    """Async token bucket pacing ALL Schwab REST calls to a sustained rate + small burst.

    Two tiers: interactive acquires need 1 token; background acquires need
    ``1 + reserve``, so they yield the reserve to live requests and spend only spare
    budget (background can never starve a live chart/quote). The WAF breaker is checked
    inside ``acquire`` so an open-breaker call fails fast without consuming a token.
    ``clock``/``sleep`` are injectable so the limiter can be tested without wall-clock
    sleeps.
    """

    def __init__(
        self,
        rate: float,
        burst: float,
        *,
        reserve: float = _BACKGROUND_RESERVE,
        clock: Any = None,
        sleep: Any = None,
    ) -> None:
        self._rate = float(rate)
        self._capacity = float(burst)
        self._reserve = float(reserve)
        self._tokens = float(burst)
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._updated = self._clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        delta = now - self._updated
        if delta > 0:
            self._tokens = min(self._capacity, self._tokens + delta * self._rate)
            self._updated = now

    async def acquire(self, *, background: bool = False) -> bool:
        probe = _waf_gate()  # breaker check first — no token spent while open
        needed = 1.0 + (self._reserve if background else 0.0)
        waited = False
        try:
            while True:
                async with self._lock:
                    self._refill()
                    if self._tokens >= needed:
                        self._tokens -= 1.0
                        if waited:
                            _note_throttled()
                        return probe
                    deficit = needed - self._tokens
                waited = True
                await self._sleep(max(deficit / self._rate, _MIN_WAIT_S))
        except BaseException:
            # Cancelled/errored while waiting for a token — release a claimed probe so
            # the breaker can't wedge in half-open.
            if probe:
                _waf_probe_aborted()
            raise


_LIMITER = _RateLimiter(_RATE_PER_SEC, _RATE_BURST)


def _reset_governor() -> None:
    """Test hook: reset the breaker + refill the shared limiter (hermetic isolation)."""
    global _waf_state, _waf_blocked_until, _throttled_total, _last_throttle_log
    _waf_state = _STATE_CLOSED
    _waf_blocked_until = 0.0
    _throttled_total = 0
    _last_throttle_log = 0.0
    _LIMITER._tokens = _LIMITER._capacity
    _LIMITER._updated = _LIMITER._clock()


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
    async def _governed_call(fn: Any, *args: Any, background: bool = False, **kwargs: Any) -> Any:
        """The single chokepoint every Schwab REST call funnels through: acquire a
        token from the global limiter (which gates on the WAF breaker first, so an
        open breaker fails fast without spending a token), dispatch the sync HTTP call
        on a worker thread, and release a claimed half-open probe if the call errored
        without a WAF verdict. No retries — the governor only PACES existing calls.
        """
        probe = await _LIMITER.acquire(background=background)
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except BaseException:
            if probe:
                _waf_probe_aborted()
            raise

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
    def _aggregate_to_4hourly(bars: list[StockBar]) -> list[StockBar]:
        if not bars:
            return []

        buckets: dict[int, list[StockBar]] = defaultdict(list)
        for bar in bars:
            dt = datetime.fromisoformat(bar.time.replace("Z", "+00:00"))
            bucket_key = int(dt.timestamp()) // (4 * 3600)
            buckets[bucket_key].append(bar)

        four_hourly: list[StockBar] = []
        for bucket_key in sorted(buckets):
            group = buckets[bucket_key]
            bucket_start = datetime.fromtimestamp(
                bucket_key * 4 * 3600, tz=UTC
            )
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
        background: bool = False,
    ) -> list[StockBar]:
        if timeframe == "1Hour":
            bars = await self._governed_call(
                self._fetch_bars_sync,
                symbol,
                timeframe="30Min",
                start=start,
                end=end,
                extended_hours=extended_hours,
                background=background,
            )
            return self._aggregate_to_hourly(bars)
        if timeframe == "4Hour":
            bars = await self._governed_call(
                self._fetch_bars_sync,
                symbol,
                timeframe="30Min",
                start=start,
                end=end,
                extended_hours=extended_hours,
                background=background,
            )
            return self._aggregate_to_4hourly(bars)
        if timeframe == "1Week":
            bars = await self._governed_call(
                self._fetch_bars_sync,
                symbol,
                timeframe="1Day",
                start=start,
                end=end,
                extended_hours=extended_hours,
                background=background,
            )
            return self._aggregate_to_weekly(bars)
        if timeframe == "1Month":
            bars = await self._governed_call(
                self._fetch_bars_sync,
                symbol,
                timeframe="1Day",
                start=start,
                end=end,
                extended_hours=extended_hours,
                background=background,
            )
            return self._aggregate_to_monthly(bars)
        return await self._governed_call(
            self._fetch_bars_sync,
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            extended_hours=extended_hours,
            background=background,
        )

    async def aget_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Day",
        days: int = 180,
        extended_hours: bool = False,
        background: bool = False,
    ) -> list[StockBar]:
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        return await self.aget_stock_bars_range(
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            extended_hours=extended_hours,
            background=background,
        )

    async def _fetch_symbol_batch(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        extended_hours: bool = False,
        background: bool = False,
    ) -> tuple[str, list[StockBar] | None]:
        # No local concurrency semaphore: the global rate limiter (acquired per symbol
        # inside aget_stock_bars_range) is now the sole pacing authority, so a batch of
        # N symbols naturally spends N tokens paced to the shared budget.
        try:
            bars = await self.aget_stock_bars_range(
                symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                extended_hours=extended_hours,
                background=background,
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

    async def aget_quotes(
        self, symbols: list[str], *, background: bool = False
    ) -> dict[str, dict]:
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
            return await self._governed_call(
                self._fetch_quotes_sync, cleaned, background=background
            )
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
        background: bool = False,
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
        results = await asyncio.gather(
            *(
                self._fetch_symbol_batch(
                    symbol,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    extended_hours=extended_hours,
                    background=background,
                )
                for symbol in cleaned
            )
        )

        out: dict[str, list[StockBar]] = {}
        for symbol, bars in results:
            if bars is not None:
                out[symbol] = bars
        return out
