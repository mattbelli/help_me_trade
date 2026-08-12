"""Session-node level compute + API endpoint tests."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from research.intraday_bars import IntradayBar
from signals.session_node_strategy import (
    compute_session_levels,
    load_session_node_context,
)

DAY1 = date(2024, 6, 3)
DAY2 = date(2024, 6, 4)


def _bar(
    ts: str,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    c: float | None = None,
    v: float = 1000.0,
) -> IntradayBar:
    close = c if c is not None else 100.0
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
    price: float = 100.0,
    v: float = 1000.0,
) -> IntradayBar:
    utc_hour = ny_hour + 4
    ts = f"{ny_date.isoformat()}T{utc_hour:02d}:{ny_minute:02d}:00+00:00"
    return _bar(ts, o=price, h=price, low=price, c=price, v=v)


def _flat_session(
    ny_date: date,
    price: float,
    count: int,
    *,
    volume: float = 1000.0,
    start_minute: int = 30,
) -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    for i in range(count):
        minute = start_minute + i
        hour = 9 + minute // 60
        minute = minute % 60
        bars.append(_rth_bar(ny_date, hour, minute, price=price, v=volume))
    return bars


def _reference_session(ny_date: date) -> list[IntradayBar]:
    return (
        _flat_session(ny_date, 85.0, 50, volume=10_000.0)
        + _flat_session(ny_date, 100.0, 10, volume=100_000.0, start_minute=80)
        + _flat_session(ny_date, 115.0, 3, volume=500.0, start_minute=90)
    )


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


def _patch_provider(monkeypatch, spy_bars: list[StockBar], *, symbol: str = "SPY") -> None:
    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(return_value={symbol: spy_bars})
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )


@pytest.mark.asyncio
async def test_compute_session_levels_returns_five_named_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _reference_session(DAY1)
    today = [_rth_bar(DAY2, 9, 30, price=100.0)]
    _patch_provider(monkeypatch, _intraday_to_stockbars(prior + today))
    # Market open: today's session is still forming → reference the prior (DAY1).
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    payload = await compute_session_levels("SPY")
    assert payload is not None
    assert payload["symbol"] == "SPY"
    assert payload["session_date"] == DAY1.isoformat()
    # 5 session nodes + PWH/PWL (the mock serves the same bars for the weekly
    # daily-bar fetch, so the prior completed week always exists here).
    assert len(payload["levels"]) == 7

    by_name = {level["name"]: level for level in payload["levels"]}
    assert by_name["POC"]["kind"] == "poc"
    assert by_name["VAH"]["kind"] == "resistance"
    assert by_name["VAL"]["kind"] == "support"
    assert by_name["PDH"]["kind"] == "prior_high"
    assert by_name["PDL"]["kind"] == "prior_low"
    assert by_name["PWH"]["kind"] == "prior_week_high"
    assert by_name["PWL"]["kind"] == "prior_week_low"
    for level in payload["levels"]:
        assert isinstance(level["price"], float)
        assert level["price"] > 0


@pytest.mark.asyncio
async def test_compute_session_levels_none_without_prior_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today_only = [_rth_bar(DAY2, 9, 30, price=100.0)]
    _patch_provider(monkeypatch, _intraday_to_stockbars(today_only))
    # Market open + only a forming session, no prior completed one → None.
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    assert await compute_session_levels("SPY") is None


@pytest.mark.asyncio
async def test_compute_session_levels_uses_latest_completed_when_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After the close / pre-market the latest session is complete → reference IT,
    # so the chart overlay + morning report are never a day stale.
    day1 = _reference_session(DAY1)
    day2 = _reference_session(DAY2)
    _patch_provider(monkeypatch, _intraday_to_stockbars(day1 + day2))
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: False)

    payload = await compute_session_levels("SPY")
    assert payload is not None
    assert payload["session_date"] == DAY2.isoformat()


@pytest.mark.asyncio
async def test_load_session_node_context_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from research.session_node_signal import compute_reference_nodes

    prior = _reference_session(DAY1)
    today = [_rth_bar(DAY2, 9, 30, price=100.0)]
    stock_bars = _intraday_to_stockbars(prior + today)
    _patch_provider(monkeypatch, stock_bars)

    ctx = await load_session_node_context("SPY")
    assert ctx is not None
    assert ctx.session_date == DAY1
    assert ctx.today_bars
    assert ctx.all_bars
    expected_nodes = compute_reference_nodes(prior)
    assert expected_nodes is not None
    assert ctx.nodes == expected_nodes


@pytest.fixture()
def test_client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_api_session_node_levels_shape(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = _reference_session(DAY1)
    today = [_rth_bar(DAY2, 9, 30, price=100.0)]
    _patch_provider(monkeypatch, _intraday_to_stockbars(prior + today))
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    resp = test_client.get("/api/session-node/levels?symbol=SPY")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "SPY"
    assert data["session_date"] == DAY1.isoformat()
    # 5 session nodes + PWH/PWL (same-bars mock → prior week always present).
    assert len(data["levels"]) == 7


def test_api_session_node_levels_empty_when_no_data(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(return_value={"SPY": []})
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )

    resp = test_client.get("/api/session-node/levels?symbol=SPY")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"symbol": "SPY", "session_date": None, "levels": []}


def test_api_session_node_levels_empty_on_broker_error(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(
        side_effect=BrokerError("market data unavailable")
    )
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )

    resp = test_client.get("/api/session-node/levels?symbol=SPY")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"symbol": "SPY", "session_date": None, "levels": []}


# --- Batch path -----------------------------------------------------------------
#
# compute_session_levels_batch exists so the fusion levels producer can sweep ~9.8K
# symbols in ~120 requests instead of ~19,750 (one per symbol, twice). The contract
# that matters is that batching changed only HOW bars are fetched: a symbol's levels
# must be byte-identical either way, because the same code also backs the live
# propose-only session_node_spy strategy.


class _RecordingProvider:
    """Provider double that serves the same bars for every symbol and records the
    (timeframe, days, symbol-count) of each call — so a test can pin request COUNT,
    which is the entire point of the batch path."""

    def __init__(self, bars: list[StockBar], *, known: set[str] | None = None) -> None:
        self._bars = bars
        self._known = known
        self.calls: list[tuple[str, int, int]] = []

    async def aget_stock_bars_batch(
        self, symbols: list[str], *, timeframe: str = "1Day", days: int = 12
    ) -> dict[str, list[StockBar]]:
        self.calls.append((timeframe, days, len(symbols)))
        return {
            sym: self._bars
            for sym in symbols
            if self._known is None or sym in self._known
        }


@pytest.mark.asyncio
async def test_batch_levels_match_the_single_symbol_path_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from signals.session_node_strategy import compute_session_levels_batch

    bars = _intraday_to_stockbars(_reference_session(DAY1) + [_rth_bar(DAY2, 9, 30)])
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    _patch_provider(monkeypatch, bars)
    single = await compute_session_levels("SPY")

    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: _RecordingProvider(bars),
    )
    batched = await compute_session_levels_batch(["SPY"])

    assert single is not None
    assert batched["SPY"] == single


@pytest.mark.asyncio
async def test_batch_skips_symbols_with_no_bars_without_losing_the_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reason no back-off is needed: a symbol that yields nothing rides along in a
    # slice for free and simply does not appear in the result — it neither costs a
    # request of its own nor prevents its slice-mates from being stamped.
    from signals.session_node_strategy import compute_session_levels_batch

    bars = _intraday_to_stockbars(_reference_session(DAY1) + [_rth_bar(DAY2, 9, 30)])
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)
    provider = _RecordingProvider(bars, known={"SPY", "QQQ"})
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider", lambda: provider
    )

    out = await compute_session_levels_batch(["SPY", "DEAD", "QQQ"])

    assert set(out) == {"SPY", "QQQ"}
    # DEAD cost no request of its own — one daily call + one minute slice, total.
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_batch_slices_the_minute_fetch_and_fetches_daily_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Minute bars are sliced for MEMORY (5 days x ~1,950 bars x 9.8K symbols would
    # materialise ~19M objects); daily bars are tiny, so they are fetched once for the
    # whole equity set rather than per slice.
    from signals.session_node_strategy import compute_session_levels_batch

    bars = _intraday_to_stockbars(_reference_session(DAY1) + [_rth_bar(DAY2, 9, 30)])
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)
    monkeypatch.setattr("signals.session_node_strategy._LEVELS_BATCH_SIZE", 2)
    provider = _RecordingProvider(bars)
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider", lambda: provider
    )

    out = await compute_session_levels_batch(["A", "B", "C", "D", "E"])

    assert len(out) == 5
    daily = [c for c in provider.calls if c[0] == "1Day"]
    minute = [c for c in provider.calls if c[0] == "1Min"]
    assert len(daily) == 1 and daily[0][2] == 5  # one call, all five symbols
    assert [c[2] for c in minute] == [2, 2, 1]  # sliced 2/2/1


@pytest.mark.asyncio
async def test_batch_routes_futures_to_their_own_longer_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Futures need 10 calendar days (Globex weekend gaps) and take no PWH/PWL, so they
    # cannot share the equities' minute request.
    from signals.session_node_strategy import compute_session_levels_batch

    bars = _intraday_to_stockbars(_reference_session(DAY1) + [_rth_bar(DAY2, 9, 30)])
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)
    provider = _RecordingProvider(bars)
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider", lambda: provider
    )

    await compute_session_levels_batch(["SPY", "/ES"])

    minute = [c for c in provider.calls if c[0] == "1Min"]
    assert sorted(c[1] for c in minute) == [5, 10]  # equities 5d, futures 10d
    # The daily (PWH/PWL) leg is equities-only — /ES must not be in it.
    daily = [c for c in provider.calls if c[0] == "1Day"]
    assert len(daily) == 1 and daily[0][2] == 1
