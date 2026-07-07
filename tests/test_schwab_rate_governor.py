"""Global Schwab REST rate governor + WAF circuit breaker — hermetic tests.

Covers the durable fix for the Akamai-WAF outage: one module-level token bucket in
front of ALL Schwab REST calls (bursts impossible by construction) unified with the
circuit breaker. Everything runs on an INJECTED clock — no test ever sleeps the real
90s cooldown or wall-clock-paces a token.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

import brokers.schwab_market_data as smd
from brokers.errors import BrokerError
from brokers.schwab_market_data import SchwabMarketData, _RateLimiter


# ---------------------------------------------------------------------------
# Test clocks (injected — no wall-clock sleeps)
# ---------------------------------------------------------------------------
class _ManualClock:
    """Sequential virtual time: `sleep` advances the clock itself. Deterministic for
    single-coroutine pacing/burst assertions."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        if delay > 0:
            self.t += delay
        await asyncio.sleep(0)


class _SteppedClock:
    """Polling virtual time for genuinely-concurrent tasks: a sleeper busy-yields until
    the clock (advanced by an external driver) reaches its wake time. No wall-clock."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        target = self.t + max(delay, 0.0)
        while self.t < target:
            await asyncio.sleep(0)


async def _drive(clock: _SteppedClock, tasks: list, step: float = 0.005) -> None:
    """Advance a stepped virtual clock until every task is done."""
    guard = 0
    while not all(t.done() for t in tasks):
        await asyncio.sleep(0)
        if all(t.done() for t in tasks):
            break
        clock.t += step
        guard += 1
        assert guard < 1_000_000, "virtual clock driver stuck"


# ---------------------------------------------------------------------------
# Rate limiter — token bucket pacing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_burst_allows_first_k_instantly_then_throttles() -> None:
    clk = _ManualClock()
    lim = _RateLimiter(rate=4.0, burst=8.0, clock=clk.now, sleep=clk.sleep)

    # The full burst (8) is served with zero time advance.
    for _ in range(8):
        await lim.acquire()
    assert clk.now() == 0.0

    # The 9th and 10th are paced at exactly 1/rate = 0.25s each.
    await lim.acquire()
    assert clk.now() == pytest.approx(0.25, abs=1e-6)
    await lim.acquire()
    assert clk.now() == pytest.approx(0.50, abs=1e-6)


@pytest.mark.asyncio
async def test_sustained_rate_paces_to_configured_budget() -> None:
    clk = _ManualClock()
    lim = _RateLimiter(rate=4.0, burst=1.0, clock=clk.now, sleep=clk.sleep)

    # burst=1 → 1 instant then strictly 1/rate spacing.
    await lim.acquire()
    for i in range(1, 9):
        await lim.acquire()
        assert clk.now() == pytest.approx(i * 0.25, abs=1e-6)


@pytest.mark.asyncio
async def test_background_yields_to_interactive_when_contended() -> None:
    clk = _ManualClock()
    lim = _RateLimiter(
        rate=4.0, burst=8.0, reserve=4.0, clock=clk.now, sleep=clk.sleep
    )

    # Drain to 4 tokens; interactive (needs 1) still flows free, background (needs 1+4)
    # must wait for the reserve to refill — it only spends spare budget.
    for _ in range(4):
        await lim.acquire()
    assert clk.now() == 0.0

    await lim.acquire(background=False)  # tokens 4 -> 3, no wait
    assert clk.now() == 0.0

    # background needs 5, has 3 → waits for 2 tokens to refill = 2/4 = 0.5s.
    await lim.acquire(background=True)
    assert clk.now() == pytest.approx(0.5, abs=1e-6)


@pytest.mark.asyncio
async def test_concurrent_cold_start_burst_bounded_and_interactive_not_starved() -> None:
    """A simulated cold-start: 40 background acquires fired at once cannot exceed the
    budget (elapsed >= (N - burst)/rate), while an interactive request launched into
    the same burst still completes promptly (served from the initial burst tokens)."""
    clk = _SteppedClock()
    lim = _RateLimiter(
        rate=4.0, burst=8.0, reserve=4.0, clock=clk.now, sleep=clk.sleep
    )

    interactive_done_at: dict[str, float] = {}

    async def background() -> None:
        await lim.acquire(background=True)

    async def interactive() -> None:
        await lim.acquire(background=False)
        interactive_done_at["t"] = clk.now()

    bg = [asyncio.ensure_future(background()) for _ in range(40)]
    inter = asyncio.ensure_future(interactive())
    tasks = [*bg, inter]
    await _drive(clk, tasks)

    # Bursts are impossible by construction: 40 calls cannot finish faster than
    # burst-instant + the rest paced at the sustained rate.
    assert clk.now() >= (40 - 8) / 4.0 - 1e-6
    # The live request was not starved behind all 40 — it grabbed an early token.
    assert interactive_done_at["t"] <= 0.5


# ---------------------------------------------------------------------------
# Circuit breaker — 3-state machine on an injected monotonic clock
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status: int | None = None) -> None:
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code and self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return {"candles": [], "empty": True}


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch):
    """Inject a controllable monotonic clock into the breaker."""
    holder = {"t": 1000.0}
    monkeypatch.setattr(smd, "_monotonic", lambda: holder["t"])
    return holder


async def _fetch(provider: SchwabMarketData) -> None:
    await provider.aget_stock_bars_range(
        "AAPL",
        timeframe="1Day",
        start=datetime(2026, 6, 1, tzinfo=UTC),
        end=datetime(2026, 6, 2, tzinfo=UTC),
    )


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.asyncio
async def test_breaker_opens_on_waf_status_and_blocks_without_calling(
    status: int, fake_clock
) -> None:
    client = MagicMock()
    client.get_price_history.return_value = _FakeResp(status)
    provider = SchwabMarketData(client)

    # First call hits Schwab, gets the WAF status → breaker opens and the error raises.
    with pytest.raises(RuntimeError):
        await _fetch(provider)
    assert client.get_price_history.call_count == 1
    assert smd._waf_state == smd._STATE_OPEN

    # While open, every call fails fast with BrokerError and makes ZERO Schwab calls.
    with pytest.raises(BrokerError, match="cooldown active"):
        await _fetch(provider)
    assert client.get_price_history.call_count == 1  # unchanged — no underlying call


@pytest.mark.asyncio
async def test_breaker_half_open_probe_after_cooldown_success_closes(
    fake_clock,
) -> None:
    client = MagicMock()
    client.get_price_history.side_effect = [_FakeResp(403), _FakeResp(None)]
    provider = SchwabMarketData(client)

    with pytest.raises(RuntimeError):
        await _fetch(provider)
    assert smd._waf_state == smd._STATE_OPEN

    # Before the cooldown elapses: still blocked, no call.
    fake_clock["t"] += smd._WAF_COOLDOWN_S - 1
    with pytest.raises(BrokerError, match="cooldown active"):
        await _fetch(provider)
    assert client.get_price_history.call_count == 1

    # After the cooldown: a single probe is allowed; it succeeds → breaker closes.
    fake_clock["t"] += 2
    await _fetch(provider)
    assert client.get_price_history.call_count == 2
    assert smd._waf_state == smd._STATE_CLOSED


@pytest.mark.asyncio
async def test_breaker_reopens_when_probe_fails_again(fake_clock) -> None:
    client = MagicMock()
    client.get_price_history.side_effect = [_FakeResp(403), _FakeResp(429)]
    provider = SchwabMarketData(client)

    with pytest.raises(RuntimeError):
        await _fetch(provider)
    assert smd._waf_state == smd._STATE_OPEN
    first_deadline = smd._waf_blocked_until

    # Cooldown elapses → probe runs, gets another WAF status → re-opens with a fresh
    # cooldown deadline.
    fake_clock["t"] += smd._WAF_COOLDOWN_S + 1
    with pytest.raises(RuntimeError):
        await _fetch(provider)
    assert smd._waf_state == smd._STATE_OPEN
    assert smd._waf_blocked_until > first_deadline
    assert client.get_price_history.call_count == 2


def test_only_one_probe_per_cooldown(fake_clock) -> None:
    """Once the cooldown elapses, exactly ONE caller claims the half-open probe; any
    concurrent caller fails fast until the probe resolves."""
    smd._waf_state = smd._STATE_OPEN
    smd._waf_blocked_until = fake_clock["t"] - 1  # cooldown already elapsed

    assert smd._waf_gate() is True  # first caller claims the probe
    assert smd._waf_state == smd._STATE_HALF_OPEN

    with pytest.raises(BrokerError, match="probe in flight"):
        smd._waf_gate()  # second caller is turned away


def test_probe_abort_reopens_breaker(fake_clock) -> None:
    """A probe that errors without a WAF verdict (e.g. a network error) stays cautious
    and re-opens the breaker rather than wedging in half-open."""
    smd._waf_state = smd._STATE_HALF_OPEN
    smd._waf_probe_aborted()
    assert smd._waf_state == smd._STATE_OPEN
    assert smd._waf_blocked_until == fake_clock["t"] + smd._WAF_COOLDOWN_S


# ---------------------------------------------------------------------------
# Fallback-preservation: an open breaker degrades gracefully (never a 500)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_open_breaker_surfaces_broker_error_on_every_entry_point(
    fake_clock,
) -> None:
    """All four async entry points must fail fast with BrokerError while the breaker is
    open (the exact failure their callers already catch to fall back to Alpaca/cache),
    and none may touch Schwab."""
    smd._waf_state = smd._STATE_OPEN
    smd._waf_blocked_until = fake_clock["t"] + smd._WAF_COOLDOWN_S
    client = MagicMock()
    provider = SchwabMarketData(client)

    with pytest.raises(BrokerError):
        await provider.aget_stock_bars_range(
            "AAPL",
            timeframe="1Day",
            start=datetime(2026, 6, 1, tzinfo=UTC),
            end=datetime(2026, 6, 2, tzinfo=UTC),
        )
    with pytest.raises(BrokerError):
        await provider.aget_stock_bars("AAPL", timeframe="1Day", days=5)
    with pytest.raises(BrokerError):
        await provider.aget_quotes(["AAPL"])

    # Batch fail-softs per symbol (its callers expect a partial dict, not a raise).
    result = await provider.aget_stock_bars_batch(["AAPL", "MSFT"], days=5)
    assert result == {}

    # Zero Schwab calls happened.
    assert client.get_price_history.call_count == 0
    assert client.get_quotes.call_count == 0


@pytest.mark.asyncio
async def test_api_bars_falls_back_to_alpaca_when_breaker_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /api/bars cache core falls back to Alpaca when the Schwab provider raises
    (open breaker) — a governed reject never 500s the endpoint."""
    import api.main as main
    from brokers.alpaca import StockBar

    class _FakeSchwab:
        async def aget_stock_bars(self, *a, **k):
            raise BrokerError("Schwab WAF cooldown active — skipping request")

    class _FakeAlpaca:
        async def aget_stock_bars(self, sym, *, timeframe, days):
            return [StockBar(time="2026-06-23T00:00:00Z", open=1, high=2, low=1, close=2, volume=9)]

    monkeypatch.setattr(main, "get_market_data_provider", lambda: _FakeSchwab())
    monkeypatch.setattr(main, "_require_alpaca", lambda: _FakeAlpaca())

    resp = await main._get_bars_cached("ZZTESTGOV", "1Day", 5)
    assert resp is not None
    assert resp["symbol"] == "ZZTESTGOV"
    assert len(resp["bars"]) == 1
