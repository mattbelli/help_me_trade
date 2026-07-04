"""Regression tests: futures symbols on /api/watchlist/market.

Verifies that:
- /ES (and any /-prefixed symbol) returns price/sparkline/volume from bars, and
  has all equity-only fields (rvol, pe, eps, div_yield, market_cap,
  last_earnings, next_earnings, ext_price, ext_change_pct) set to None.
- An equity symbol (AAPL) in the same request is unaffected: its enrichment
  path still runs (rvol key present, possibly None from missing profile, but
  the key exists and the equity path is taken).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import StockBar


def _bar(t: str, close: float, volume: float = 1_000_000.0) -> StockBar:
    return StockBar(time=t, open=close, high=close + 1, low=close - 1, close=close, volume=volume)


def _make_provider() -> MagicMock:
    """Minimal provider stub with aget_stock_bars_batch returning two symbols."""
    from brokers.schwab_market_data import SchwabMarketData

    provider = MagicMock(spec=SchwabMarketData)

    es_daily = [
        _bar("2026-06-26T00:00:00Z", 5_400.0),
        _bar("2026-06-27T00:00:00Z", 5_420.0),
        _bar("2026-06-28T00:00:00Z", 5_450.0),
    ]
    aapl_daily = [
        _bar("2026-06-26T00:00:00Z", 195.0),
        _bar("2026-06-27T00:00:00Z", 197.0),
        _bar("2026-06-28T00:00:00Z", 199.0),
    ]
    es_intraday = [_bar("2026-06-28T14:00:00Z", 5_445.0, 50_000.0)]
    aapl_intraday = [_bar("2026-06-28T14:00:00Z", 198.5, 80_000.0)]

    # Both daily and intraday requests go through aget_stock_bars_batch.
    # Differentiate by the 'timeframe' kwarg.
    async def _batch(symbols: list[str], timeframe: str = "1Day", days: int = 12):
        if timeframe == "5Min":
            result: dict[str, list[StockBar]] = {}
            if "/ES" in symbols:
                result["/ES"] = es_intraday
            if "AAPL" in symbols:
                result["AAPL"] = aapl_intraday
            return result
        # 1Day
        result = {}
        if "/ES" in symbols:
            result["/ES"] = es_daily
        if "AAPL" in symbols:
            result["AAPL"] = aapl_daily
        return result

    provider.aget_stock_bars_batch = _batch
    # aget_quotes returns empty (no ext fields needed for the test).
    provider.aget_quotes = AsyncMock(return_value={})
    return provider


@pytest.fixture()
def test_client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_futures_symbol_price_populated_equity_fields_none(
    test_client, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/watchlist/market?symbols=/ES,AAPL — futures get price, equities untouched."""
    provider = _make_provider()
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)
    # Fix session so ext logic is deterministic.
    monkeypatch.setattr("api.main._market_session", lambda: "regular")
    # Stub out apply_extended so it doesn't hit the real hub.
    monkeypatch.setattr(
        "api.main.market_hub.apply_extended",
        AsyncMock(),
    )
    # Isolate the volume-profile store so futures RVOL is deterministic — with no
    # profile built it's None (warming). RVOL is a real futures feature now (built
    # lazily from a Globex profile), not a hardcoded None.
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))

    resp = test_client.get("/api/watchlist/market?symbols=/ES,AAPL")
    assert resp.status_code == 200
    quotes = resp.json()["quotes"]

    # /ES must be present with correct last-bar price.
    assert "/ES" in quotes
    es = quotes["/ES"]
    assert es["price"] == pytest.approx(5_450.0)
    assert es["change_pct"] == pytest.approx(round((5_450.0 - 5_420.0) / 5_420.0 * 100, 2))

    # Fundamentals/earnings/extended session don't exist for futures -> always None.
    for field in ("pe", "eps", "div_yield", "market_cap", "last_earnings", "next_earnings"):
        assert es[field] is None, f"Expected {field}=None for /ES, got {es[field]}"
    assert es["ext_price"] is None
    assert es["ext_change_pct"] is None
    # RVOL is profile-driven; with the store isolated to an empty dir, no profile
    # exists yet -> None (warming). The key is always present.
    assert "rvol" in es
    assert es["rvol"] is None

    # AAPL must still be present and have the rvol key (equity path ran).
    assert "AAPL" in quotes
    aapl = quotes["AAPL"]
    assert aapl["price"] == pytest.approx(199.0)
    assert "rvol" in aapl  # key exists (may be None if no profile, but key must be there)
    # Equity-only fields are present (not forcibly None).
    assert "pe" in aapl
    assert "eps" in aapl


def test_session_intraday_closes_futures_uses_latest_globex_session() -> None:
    """Futures sparkline data = the latest Globex session (incl. overnight), not RTH.

    RTH filtering returned [] off-hours, which the sparkline rendered as no-data.
    """
    from api.main import _session_intraday_closes

    bars = [
        _bar("2026-06-29T14:00:00Z", 100.0),  # 10:00 ET 06-29 -> PRIOR Globex session
        _bar("2026-06-29T22:00:00Z", 200.0),  # 18:00 ET 06-29 -> latest session (evening)
        _bar("2026-06-30T06:00:00Z", 210.0),  # 02:00 ET 06-30 -> latest session (overnight)
    ]
    closes = _session_intraday_closes(bars, futures=True)
    assert closes == [200.0, 210.0]  # latest session only, and the 18:00 evening bar IS kept


def test_session_intraday_closes_equity_rth_unchanged() -> None:
    """Equity path (default) still RTH-filters 09:30-16:00 ET."""
    from api.main import _session_intraday_closes

    bars = [
        _bar("2026-06-29T13:30:00Z", 100.0),  # 09:30 ET -> RTH, kept
        _bar("2026-06-29T20:30:00Z", 200.0),  # 16:30 ET -> after close, excluded
    ]
    assert _session_intraday_closes(bars) == [100.0]


def test_empty_bar_batch_returns_502(
    test_client, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A total bar-fetch failure (e.g. stale provider auth) must return 502,
    not 200 with empty quotes — clients keep last-good data on error."""
    provider = _make_provider()

    async def _empty_batch(symbols: list[str], timeframe: str = "1Day", days: int = 12):
        return {}

    provider.aget_stock_bars_batch = _empty_batch
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))

    resp = test_client.get("/api/watchlist/market?symbols=AAPL,/ES")
    assert resp.status_code == 502
    assert "market data unavailable" in resp.json()["error"]
