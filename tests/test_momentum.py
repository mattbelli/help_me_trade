"""Momentum daily bars persistence and ranking entry point."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import StockBar
from db.momentum_bars import (
    latest_bar_date,
    load_daily_bars,
    upsert_daily_bars,
)
from research.momentum_reference import compute_target_set


def _trading_dates(end: str, count: int) -> list[str]:
    d = date.fromisoformat(end)
    out: list[str] = []
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    out.reverse()
    return out


def _synthetic_bar_rows(
    symbols: list[str],
    *,
    end: str = "2024-12-31",
    n_days: int = 280,
) -> tuple[list[tuple[str, str, float, float]], str]:
    dates = _trading_dates(end, n_days)
    rows: list[tuple[str, str, float, float]] = []
    for i, sym in enumerate(symbols):
        base = 50.0 + i
        for j, dt in enumerate(dates):
            close = base * (1.001 ** (j * (1.0 + i * 0.05)))
            open_ = close * 0.999
            rows.append((sym, dt, open_, close))
    return rows, dates[-1]


def test_upsert_is_idempotent_and_updates_prices(tmp_path: Path) -> None:
    db = tmp_path / "mom.db"
    upsert_daily_bars([("AAPL", "2024-01-02", 100.0, 101.0)], db_path=db)
    upsert_daily_bars([("AAPL", "2024-01-02", 100.0, 101.0)], db_path=db)
    upsert_daily_bars([("AAPL", "2024-01-02", 105.0, 106.0)], db_path=db)

    loaded = load_daily_bars(["AAPL"], db_path=db)
    dates, closes, opens = loaded["AAPL"]
    assert dates == ["2024-01-02"]
    assert opens == [105.0]
    assert closes == [106.0]


def test_load_daily_bars_returns_ascending_shape(tmp_path: Path) -> None:
    db = tmp_path / "mom.db"
    rows = [
        ("MSFT", "2024-01-05", 10.0, 11.0),
        ("MSFT", "2024-01-02", 8.0, 9.0),
        ("MSFT", "2024-01-03", 9.0, 10.0),
    ]
    upsert_daily_bars(rows, db_path=db)

    loaded = load_daily_bars(["MSFT"], db_path=db)
    dates, closes, opens = loaded["MSFT"]
    assert dates == ["2024-01-02", "2024-01-03", "2024-01-05"]
    assert opens == [8.0, 9.0, 10.0]
    assert closes == [9.0, 10.0, 11.0]


def test_latest_bar_date(tmp_path: Path) -> None:
    db = tmp_path / "mom.db"
    upsert_daily_bars(
        [
            ("NVDA", "2024-01-02", 1.0, 2.0),
            ("NVDA", "2024-01-10", 3.0, 4.0),
        ],
        db_path=db,
    )
    assert latest_bar_date("NVDA", db_path=db) == "2024-01-10"
    assert latest_bar_date("missing", db_path=db) is None


def test_production_target_set_returns_top_quintile(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "mom.db"
    symbols = [
        "AAA1",
        "AAA2",
        "AAA3",
        "BBB1",
        "BBB2",
        "BBB3",
        "CCC1",
        "CCC2",
        "CCC3",
        "DDD1",
        "DDD2",
        "DDD3",
        "EEE1",
        "EEE2",
        "EEE3",
        "FFF1",
        "FFF2",
        "FFF3",
        "GGG1",
        "GGG2",
        "GGG3",
        "HHH1",
        "HHH2",
        "HHH3",
        "III1",
        "III2",
        "III3",
    ]
    sector_map = {
        "AAA1": "SectorA",
        "AAA2": "SectorA",
        "AAA3": "SectorA",
        "BBB1": "SectorB",
        "BBB2": "SectorB",
        "BBB3": "SectorB",
        "CCC1": "SectorC",
        "CCC2": "SectorC",
        "CCC3": "SectorC",
        "DDD1": "SectorD",
        "DDD2": "SectorD",
        "DDD3": "SectorD",
        "EEE1": "SectorE",
        "EEE2": "SectorE",
        "EEE3": "SectorE",
        "FFF1": "SectorF",
        "FFF2": "SectorF",
        "FFF3": "SectorF",
        "GGG1": "SectorG",
        "GGG2": "SectorG",
        "GGG3": "SectorG",
        "HHH1": "SectorH",
        "HHH2": "SectorH",
        "HHH3": "SectorH",
        "III1": "SectorI",
        "III2": "SectorI",
        "III3": "SectorI",
    }
    rows, as_of = _synthetic_bar_rows(symbols)
    upsert_daily_bars(rows, db_path=db)

    monkeypatch.setattr("signals.momentum.resolve_universe_spec", lambda _spec: symbols)
    monkeypatch.setattr("signals.momentum.load_sector_map", lambda: sector_map)

    from signals.momentum import production_target_set

    result = production_target_set(as_of=as_of, db_path=db)
    bars = load_daily_bars(symbols, db_path=db)
    expected = compute_target_set(bars, sector_map, as_of)

    assert result == expected
    assert result
    assert len(result) <= len(symbols)
    assert result[0]["residual_momentum"] >= result[-1]["residual_momentum"]
    assert all("symbol" in row and "residual_momentum" in row for row in result)


@pytest.mark.asyncio
async def test_refresh_universe_bars_upserts_provider_bars(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "mom.db"
    monkeypatch.setattr(
        "signals.momentum.resolve_universe_spec",
        lambda _spec: ["AAPL", "MSFT"],
    )

    provider = AsyncMock()
    provider.aget_stock_bars_batch = AsyncMock(
        return_value={
            "AAPL": [
                StockBar(
                    time="2024-12-30T05:00:00Z",
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1.0,
                )
            ],
            "MSFT": [
                StockBar(
                    time="2024-12-30T05:00:00Z",
                    open=200.0,
                    high=201.0,
                    low=199.0,
                    close=200.5,
                    volume=1.0,
                )
            ],
        }
    )
    monkeypatch.setattr("signals.momentum.get_market_data_provider", lambda: provider)

    from signals.momentum import refresh_universe_bars

    n = await refresh_universe_bars(days=30, db_path=db)
    assert n == 2
    loaded = load_daily_bars(["AAPL", "MSFT"], db_path=db)
    assert loaded["AAPL"][0] == ["2024-12-30"]
    assert loaded["MSFT"][1][0] == 200.5


@pytest.mark.asyncio
async def test_refresh_universe_bars_fail_soft_on_missing_symbol(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "mom.db"
    monkeypatch.setattr(
        "signals.momentum.resolve_universe_spec",
        lambda _spec: ["AAPL", "BAD"],
    )

    provider = AsyncMock()
    provider.aget_stock_bars_batch = AsyncMock(
        return_value={
            "AAPL": [
                StockBar(
                    time="2024-12-30T05:00:00Z",
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                )
            ],
        }
    )
    monkeypatch.setattr("signals.momentum.get_market_data_provider", lambda: provider)

    from signals.momentum import refresh_universe_bars

    n = await refresh_universe_bars(days=30, db_path=db)
    assert n == 1
    assert "AAPL" in load_daily_bars(["AAPL", "BAD"], db_path=db)


def test_extreme_residual_gate_drops_artifacts_and_flags_boundary() -> None:
    """Calibrated screen: hard-drop residual > +12, soft-flag > +8 (keep), keep the rest."""
    from signals.momentum import _apply_extreme_residual_gate

    target = [
        {"symbol": "OK", "residual_momentum": 3.0},
        {"symbol": "FLAG", "residual_momentum": 9.0},   # > +8 soft → kept + logged
        {"symbol": "EDGE", "residual_momentum": 12.0},  # exactly +12 → kept (not strictly >)
        {"symbol": "SNDK", "residual_momentum": 32.0},  # > +12 hard → dropped
    ]
    out = _apply_extreme_residual_gate(target)
    syms = {d["symbol"] for d in out}
    assert "SNDK" not in syms
    assert syms == {"OK", "FLAG", "EDGE"}
