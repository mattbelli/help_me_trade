"""Futures (Globex) session-node: overnight range must be included, not RTH-only.

The prior-session levels for a futures symbol like /ES should reflect the full
~23h CME Globex session (18:00 ET prev-day → 17:00 ET).  Before this fix, the
code called segment_rth_sessions (09:30–16:00 ET only), dropping the overnight
~14.5h and silently producing wrong PDH/PDL.

These tests use synthetic 1-min bars with no network calls (provider is mocked).
Equity behaviour is also asserted to be byte-identical to before.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import StockBar
from research.intraday_bars import IntradayBar
from research.intraday_profile import to_ny
from signals.session_node_strategy import compute_session_levels

# EDT = UTC-4 (all test dates are in June 2024)
#
# Prior complete Globex session → keyed as 2024-06-04 (Tuesday) by segment_globex_sessions:
#   session opens  18:00 EDT Mon 2024-06-03  = 22:00 UTC 2024-06-03
#   session closes 17:00 EDT Tue 2024-06-04  = 21:00 UTC 2024-06-04
#
# Current partial session (Wednesday 2024-06-05) provides the "today" anchor so that
# Tuesday is unambiguously the *prior* complete session.

_PRIOR_GLOBEX_DATE = date(2024, 6, 4)   # Tuesday — the complete prior session
_CURRENT_DATE = date(2024, 6, 5)         # Wednesday — partial current session

# Price landmarks
OVERNIGHT_EXTREME_HIGH = 5300.0  # overnight spike at 03:00 EDT Tue; ABOVE any RTH level
OVERNIGHT_EXTREME_LOW = 4800.0   # overnight dip  at 02:00 EDT Tue; BELOW any RTH level
RTH_HIGH = 5050.0                # highest intraday RTH tick (well inside Globex range)
RTH_LOW = 4950.0                 # lowest intraday RTH tick  (well inside Globex range)
RTH_BASE = 5000.0                # bulk-volume RTH price → POC cluster lives here


# ---------------------------------------------------------------------------
# Bar helpers
# ---------------------------------------------------------------------------

def _bar(
    ts: str,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    c: float | None = None,
    v: float = 1000.0,
) -> IntradayBar:
    close = c if c is not None else RTH_BASE
    return {
        "t": ts,
        "o": o if o is not None else close,
        "h": h if h is not None else close,
        "l": low if low is not None else close,
        "c": close,
        "v": v,
    }


def _rth_bar(
    ny_date: date,
    ny_hour: int,
    ny_minute: int,
    *,
    price: float = RTH_BASE,
    v: float = 1000.0,
) -> IntradayBar:
    """Bar at the given NY time; June = EDT so UTC = NY + 4h."""
    utc_hour = ny_hour + 4
    ts = f"{ny_date.isoformat()}T{utc_hour:02d}:{ny_minute:02d}:00+00:00"
    return _bar(ts, o=price, h=price, low=price, c=price, v=v)


def _intraday_to_stockbars(bars: list[IntradayBar]) -> list[StockBar]:
    out: list[StockBar] = []
    for bar in bars:
        ts = bar["t"].replace("+00:00", "Z")
        out.append(
            StockBar(
                time=ts,
                open=bar["o"],
                high=bar["h"],
                low=bar["l"],
                close=bar["c"],
                volume=bar["v"],
            )
        )
    return out


def _build_prior_session_bars() -> list[IntradayBar]:
    """Full bar list for the prior Globex session (Tuesday 2024-06-04).

    Contains:
    - An overnight open bar at 18:00 EDT Monday (22:00 UTC Monday).
    - An overnight low dip at 02:00 EDT Tuesday (06:00 UTC Tuesday) — BELOW RTH_LOW.
    - An overnight high spike at 03:00 EDT Tuesday (07:00 UTC Tuesday) — ABOVE RTH_HIGH.
    - 20 RTH bars from 09:30 EDT Tuesday at RTH_BASE (bulk volume; POC lives here).
    - One RTH high tick and one RTH low tick inside the RTH window.

    RTH-only segmentation would see PDH=RTH_HIGH (5050) and PDL=RTH_LOW (4950).
    Full Globex segmentation must see PDH=OVERNIGHT_EXTREME_HIGH (5300) and
    PDL=OVERNIGHT_EXTREME_LOW (4800).
    """
    bars: list[IntradayBar] = []

    # Overnight open (18:00 EDT Monday = 22:00 UTC Monday)
    bars.append(_bar(
        "2024-06-03T22:00:00+00:00",
        o=RTH_BASE, h=RTH_BASE, low=RTH_BASE, c=RTH_BASE, v=200.0,
    ))

    # Overnight dip (02:00 EDT Tuesday = 06:00 UTC Tuesday) — below RTH_LOW
    bars.append(_bar(
        "2024-06-04T06:00:00+00:00",
        o=OVERNIGHT_EXTREME_LOW, h=OVERNIGHT_EXTREME_LOW,
        low=OVERNIGHT_EXTREME_LOW, c=OVERNIGHT_EXTREME_LOW, v=150.0,
    ))

    # Overnight spike (03:00 EDT Tuesday = 07:00 UTC Tuesday) — above RTH_HIGH
    bars.append(_bar(
        "2024-06-04T07:00:00+00:00",
        o=OVERNIGHT_EXTREME_HIGH, h=OVERNIGHT_EXTREME_HIGH,
        low=OVERNIGHT_EXTREME_HIGH, c=OVERNIGHT_EXTREME_HIGH, v=200.0,
    ))

    # RTH bulk-volume bars 09:30–09:49 EDT Tuesday (20 bars at RTH_BASE)
    for i in range(20):
        total_minute = 30 + i
        bars.append(_rth_bar(
            _PRIOR_GLOBEX_DATE,
            9 + total_minute // 60,
            total_minute % 60,
            price=RTH_BASE,
            v=10_000.0,
        ))

    # RTH high tick at 10:00 EDT (highest RTH price, still < OVERNIGHT_EXTREME_HIGH)
    bars.append(_rth_bar(_PRIOR_GLOBEX_DATE, 10, 0, price=RTH_HIGH, v=300.0))

    # RTH low tick at 10:30 EDT (lowest RTH price, still > OVERNIGHT_EXTREME_LOW)
    bars.append(_rth_bar(_PRIOR_GLOBEX_DATE, 10, 30, price=RTH_LOW, v=300.0))

    return bars


def _patch_provider(monkeypatch: pytest.MonkeyPatch, symbol: str, bars: list[IntradayBar]) -> None:
    stock_bars = _intraday_to_stockbars(bars)
    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(return_value={symbol: stock_bars})
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_futures_session_includes_overnight_extreme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Futures /ES: PDH and PDL must reflect the FULL Globex session, not RTH-only.

    The prior Globex session contains an overnight high of 5300 and an overnight
    low of 4800.  RTH-only segmentation would cap PDH at 5050 and PDL at 4950.
    With correct Globex segmentation: PDH=5300, PDL=4800.
    """
    prior_bars = _build_prior_session_bars()
    # One current-session bar so Tuesday is unambiguously the *prior* session.
    current_bar = _rth_bar(_CURRENT_DATE, 9, 30, price=RTH_BASE)
    all_bars = prior_bars + [current_bar]

    _patch_provider(monkeypatch, "/ES", all_bars)
    # Market is open → current session is still forming; reference the prior completed one.
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    payload = await compute_session_levels("/ES")
    assert payload is not None, "compute_session_levels returned None for /ES"
    assert payload["symbol"] == "/ES"
    assert len(payload["levels"]) == 5

    by_name = {lv["name"]: lv["price"] for lv in payload["levels"]}

    # Core assertion: overnight extremes MUST be captured in PDH / PDL.
    assert by_name["PDH"] == OVERNIGHT_EXTREME_HIGH, (
        f"Expected PDH={OVERNIGHT_EXTREME_HIGH} (overnight Globex high), "
        f"got {by_name['PDH']} — overnight range was dropped (RTH-only bug not fixed)"
    )
    assert by_name["PDL"] == OVERNIGHT_EXTREME_LOW, (
        f"Expected PDL={OVERNIGHT_EXTREME_LOW} (overnight Globex low), "
        f"got {by_name['PDL']} — overnight range was dropped (RTH-only bug not fixed)"
    )

    # POC must land inside the RTH bulk-volume cluster (most volume was there).
    poc = by_name["POC"]
    assert RTH_LOW <= poc <= RTH_HIGH, (
        f"POC {poc} is outside the expected RTH volume cluster [{RTH_LOW}, {RTH_HIGH}]"
    )


@pytest.mark.asyncio
async def test_equity_session_unchanged_excludes_overnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equity SPY: overnight bars must NOT contribute to PDH/PDL (RTH-only, unchanged).

    Using the exact same raw bars as the futures test.  With RTH segmentation the
    overnight extremes (5300 / 4800) are excluded; PDH should be the intraday RTH
    high (5050) and PDL the intraday RTH low (4950).
    """
    prior_bars = _build_prior_session_bars()
    current_bar = _rth_bar(_CURRENT_DATE, 9, 30, price=RTH_BASE)
    all_bars = prior_bars + [current_bar]

    _patch_provider(monkeypatch, "SPY", all_bars)
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    payload = await compute_session_levels("SPY")
    assert payload is not None, "compute_session_levels returned None for SPY"

    by_name = {lv["name"]: lv["price"] for lv in payload["levels"]}

    # Overnight extremes must be EXCLUDED from the equity RTH session.
    assert by_name["PDH"] < OVERNIGHT_EXTREME_HIGH, (
        f"Equity PDH {by_name['PDH']} must be below overnight extreme {OVERNIGHT_EXTREME_HIGH}"
    )
    assert by_name["PDL"] > OVERNIGHT_EXTREME_LOW, (
        f"Equity PDL {by_name['PDL']} must be above overnight extreme {OVERNIGHT_EXTREME_LOW}"
    )

    # Exact RTH high/low (the only bars segment_rth_sessions includes from the prior session).
    assert by_name["PDH"] == RTH_HIGH, (
        f"Expected equity PDH={RTH_HIGH} (RTH high tick only), got {by_name['PDH']}"
    )
    assert by_name["PDL"] == RTH_LOW, (
        f"Expected equity PDL={RTH_LOW} (RTH low tick only), got {by_name['PDL']}"
    )


# ---------------------------------------------------------------------------
# Weekly levels (PWH/PWL) — prior COMPLETED week from daily bars, equities only
# ---------------------------------------------------------------------------

def _daily_stockbar(day: date, *, high: float, low: float) -> StockBar:
    """Daily bar stamped at 05:00 UTC = midnight ET on `day` (matches Alpaca)."""
    mid = (high + low) / 2
    return StockBar(
        time=f"{day.isoformat()}T05:00:00Z",
        open=mid, high=high, low=low, close=mid, volume=1_000_000.0,
    )


def _patch_provider_with_daily(
    monkeypatch: pytest.MonkeyPatch,
    symbol: str,
    minute_bars: list[IntradayBar],
    daily_bars: list[StockBar] | Exception,
) -> None:
    """Timeframe-aware provider mock: 1Min → session bars, 1Day → daily bars."""
    minute_stock = _intraday_to_stockbars(minute_bars)

    async def _batch(symbols, *, timeframe, days, **kwargs):
        if timeframe == "1Day":
            if isinstance(daily_bars, Exception):
                raise daily_bars
            return {symbol: daily_bars}
        return {symbol: minute_stock}

    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(side_effect=_batch)
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )


@pytest.mark.asyncio
async def test_equity_weekly_levels_from_prior_completed_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PWH/PWL are the prior COMPLETED Mon–Fri extremes — current-week bars and
    older weeks must both be excluded."""
    today = to_ny(datetime.now(UTC).isoformat()).date()
    this_monday = today - timedelta(days=today.weekday())
    prior_monday = this_monday - timedelta(days=7)
    older_monday = this_monday - timedelta(days=14)

    daily = [
        # Two weeks back: extremes that must NOT win.
        _daily_stockbar(older_monday, high=500.0, low=0.5),
        # Prior completed week (Mon–Fri): PWH=90 (Wed), PWL=3 (Wed).
        _daily_stockbar(prior_monday, high=10.0, low=5.0),
        _daily_stockbar(prior_monday + timedelta(days=1), high=20.0, low=8.0),
        _daily_stockbar(prior_monday + timedelta(days=2), high=90.0, low=3.0),
        _daily_stockbar(prior_monday + timedelta(days=3), high=15.0, low=6.0),
        _daily_stockbar(prior_monday + timedelta(days=4), high=12.0, low=7.0),
    ]
    # Current (incomplete) week: extremes that must be excluded. Only add days
    # strictly before today so the fixture is valid on any weekday.
    for offset in range((today - this_monday).days):
        daily.append(
            _daily_stockbar(this_monday + timedelta(days=offset), high=200.0, low=1.0)
        )

    prior_bars = _build_prior_session_bars()
    current_bar = _rth_bar(_CURRENT_DATE, 9, 30, price=RTH_BASE)
    _patch_provider_with_daily(monkeypatch, "SPY", prior_bars + [current_bar], daily)
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    payload = await compute_session_levels("SPY")
    assert payload is not None, "compute_session_levels returned None for SPY"

    by_name = {lv["name"]: lv for lv in payload["levels"]}
    assert "PWH" in by_name and "PWL" in by_name, (
        f"weekly levels missing from {sorted(by_name)}"
    )
    assert by_name["PWH"]["price"] == 90.0, (
        f"PWH must be the prior completed week's high (90), got {by_name['PWH']['price']}"
    )
    assert by_name["PWL"]["price"] == 3.0, (
        f"PWL must be the prior completed week's low (3), got {by_name['PWL']['price']}"
    )
    assert by_name["PWH"]["kind"] == "prior_week_high"
    assert by_name["PWL"]["kind"] == "prior_week_low"


@pytest.mark.asyncio
async def test_weekly_levels_failure_soft_keeps_session_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daily-bar fetch failure must never cost the session nodes."""
    prior_bars = _build_prior_session_bars()
    current_bar = _rth_bar(_CURRENT_DATE, 9, 30, price=RTH_BASE)
    _patch_provider_with_daily(
        monkeypatch, "SPY", prior_bars + [current_bar],
        RuntimeError("daily bars unavailable"),
    )
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    payload = await compute_session_levels("SPY")
    assert payload is not None, "session levels must survive a weekly-fetch failure"

    names = {lv["name"] for lv in payload["levels"]}
    assert {"POC", "VAH", "VAL", "PDH", "PDL"} <= names
    assert "PWH" not in names and "PWL" not in names
