"""Δ% is ToS "Mark % change": one reference close (yesterday's regular close)
for the whole trading day — regular, post-market, pre-midnight overnight and the
weekend — so after the bell a row shows the day's move + the after-hours move as
ONE number, never "since the 4pm close". After midnight ET / pre-market the
reference rolls to the last completed close (2026-08-17).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import StockBar
from brokers.market_stream import MarketStreamHub, QuoteState, _daily_prev_close
from trading.market_hours import _ET


def _bar(t: str, close: float) -> StockBar:
    return StockBar(time=t, open=close, high=close + 1, low=close - 1, close=close, volume=1e6)


def _series(last_day: datetime, closes: list[float]) -> list[StockBar]:
    """Daily bars ending on ``last_day`` (ET midnight stamps like the vendor)."""
    out = []
    for i, c in enumerate(reversed(closes)):
        midnight = (last_day - timedelta(days=i)).replace(hour=0, minute=0, second=0)
        out.append(_bar(midnight.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), c))
    return list(reversed(out))


def test_reference_is_yesterday_while_todays_bar_exists() -> None:
    today = datetime.now(_ET)
    bars = _series(today, [98.0, 100.0, 103.0])  # bars[-1] = today
    for session in ("regular", "post", "overnight"):
        assert _daily_prev_close(bars, session) == 100.0, session


def test_reference_rolls_to_last_completed_close_on_a_new_day() -> None:
    yesterday = datetime.now(_ET) - timedelta(days=1)
    bars = _series(yesterday, [98.0, 100.0, 103.0])  # series ends yesterday
    for session in ("pre", "overnight"):
        assert _daily_prev_close(bars, session) == 103.0, session


def test_weekend_keeps_fridays_day_change() -> None:
    friday = datetime.now(_ET) - timedelta(days=2)
    bars = _series(friday, [98.0, 100.0, 103.0])
    assert _daily_prev_close(bars, "closed") == 100.0


@pytest.mark.asyncio
async def test_hub_apply_extended_measures_vs_day_reference() -> None:
    """A seeded state ignores the caller's since-close %: 4pm close 103 (+3% on
    the day), AH print 104 → +4.0% (vs 100), not +0.97% (vs 103)."""
    hub = MarketStreamHub()
    hub._quotes["AAPL"] = QuoteState(price=103.0, change_pct=3.0, prev_close=100.0)
    await hub.apply_extended(
        "AAPL", ext_price=104.0, ext_change_pct=0.97, market_session="post", broadcast=False
    )
    assert hub._quotes["AAPL"].ext_change_pct == pytest.approx(4.0)


@pytest.fixture(autouse=True)
def _clear_wl_bars_cache():
    from api import watchlist_routes as wl_routes

    wl_routes._wl_bars_cache.clear()
    yield
    wl_routes._wl_bars_cache.clear()


def test_route_post_market_pct_is_vs_yesterday_close(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from api.main import app
    from brokers.schwab_market_data import SchwabMarketData

    today = datetime.now(_ET)
    daily = _series(today, [98.0, 100.0, 103.0])
    provider = MagicMock(spec=SchwabMarketData)

    async def _batch(symbols, timeframe="1Day", days=12):
        return {"AAPL": daily} if timeframe == "1Day" else {}

    provider.aget_stock_bars_batch = _batch
    # Provider hands back the since-close convention (104 vs 103 = +0.97%).
    provider.aget_quotes = AsyncMock(
        return_value={"AAPL": {"ext_last": 104.0, "ext_change_pct": 0.97}}
    )
    monkeypatch.setattr("api.watchlist_routes.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("api.watchlist_routes._market_session", lambda: "post")
    monkeypatch.setattr("api.main.market_hub.apply_extended", AsyncMock())
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))

    with TestClient(app, raise_server_exceptions=True) as client:
        q = client.get("/api/watchlist/market?symbols=AAPL").json()["quotes"]["AAPL"]
    assert q["prev_close"] == pytest.approx(100.0)
    assert q["change_pct"] == pytest.approx(3.0)
    assert q["ext_price"] == pytest.approx(104.0)
    assert q["ext_change_pct"] == pytest.approx(4.0)  # day move + AH move, one number
