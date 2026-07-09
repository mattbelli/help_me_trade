"""Hermetic tests for options-flow foundation (constants + DB + Alpaca read-only)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from brokers.alpaca import AlpacaAdapter
from db import options_flow as of_db
from signals.options_flow import constants as C

# --- constants -------------------------------------------------------------------


def test_constants_positive_and_ordered() -> None:
    assert C.VOL_OI_RATIO_MIN > 0
    assert C.MIN_CONTRACTS > 0
    assert C.MIN_OI > 0
    assert C.MIN_PRICE > 0
    assert C.MIN_PREMIUM_USD > 0
    assert C.MIN_PREMIUM_INDEX_USD > C.MIN_PREMIUM_USD
    assert C.VOL_ZSCORE_MIN > 0
    assert C.ZSCORE_STD_FLOOR >= 0.0
    assert C.BLOCK_MIN_CONTRACTS > 0
    assert C.BLOCK_MIN_PREMIUM_USD > 0
    assert C.EVENTS_PRUNE_DAYS > 0
    assert C.DAILY_PRUNE_DAYS > C.EVENTS_PRUNE_DAYS
    assert C.OPTIONS_FLOW_MAX_UNDERLYINGS > 0
    assert C.ZERO_DTE_EXCLUDE is True
    assert "SPY" in C.INDEX_MEGACAP_SET

    prev_hi = 0
    for lo, hi in C.DTE_BUCKETS:
        assert lo <= hi
        assert lo > prev_hi
        prev_hi = hi


def test_dte_bucket_boundaries() -> None:
    assert C.dte_bucket(0) == "out"
    assert C.dte_bucket(1) == "out"
    assert C.dte_bucket(2) == "2-7"
    assert C.dte_bucket(7) == "2-7"
    assert C.dte_bucket(8) == "8-30"
    assert C.dte_bucket(30) == "8-30"
    assert C.dte_bucket(31) == "31-90"
    assert C.dte_bucket(90) == "31-90"
    assert C.dte_bucket(91) == "91-10000"
    assert C.dte_bucket(365) == "91-10000"


# --- db --------------------------------------------------------------------------


def _sample_event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "of_2026-07-09_NVDA_0001",
        "ts": "2026-07-09T15:30:00+00:00",
        "underlying": "NVDA",
        "occ_symbol": "NVDA260821C00190000",
        "right": "call",
        "strike": 190.0,
        "expiry": "2026-08-21",
        "dte": 43,
        "otm_pct": 8.2,
        "event_type": "unusual_volume",
        "contracts": 4100,
        "premium_usd": 1435000.0,
        "vol_oi_ratio": 6.1,
        "vol_zscore": 4.2,
        "direction": "buy_side",
        "direction_basis": "polled_quote",
        "spread_flag": False,
        "n_prints": 37,
        "exchanges": ["C", "N", "M", "E"],
        "reason": "test reason",
        "caveat": "test caveat",
        "ret_1d": None,
        "ret_5d": None,
    }
    base.update(overrides)
    return base


def test_db_insert_and_recent_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    event = _sample_event()
    of_db.insert_event(event, db_path=db)

    rows = of_db.recent_events(db_path=db)
    assert len(rows) == 1
    got = rows[0]
    assert got["id"] == event["id"]
    assert got["underlying"] == "NVDA"
    assert got["event_type"] == "unusual_volume"
    assert got["premium_usd"] == 1435000.0
    assert got["exchanges"] == ["C", "N", "M", "E"]
    assert got["spread_flag"] is False
    assert "win_prob" not in got
    assert "ev" not in got
    assert "confidence" not in got


def test_db_recent_filters_and_limit_cap(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    of_db.insert_event(
        _sample_event(id="e1", underlying="NVDA", event_type="unusual_volume",
                      premium_usd=50_000, ts="2026-07-09T10:00:00+00:00"),
        db_path=db,
    )
    of_db.insert_event(
        _sample_event(id="e2", underlying="AAPL", event_type="block",
                      premium_usd=200_000, ts="2026-07-09T12:00:00+00:00"),
        db_path=db,
    )
    of_db.insert_event(
        _sample_event(id="e3", underlying="NVDA", event_type="block",
                      premium_usd=10_000, ts="2026-07-08T09:00:00+00:00"),
        db_path=db,
    )

    by_sym = of_db.recent_events(symbol="nvda", db_path=db)
    assert {r["id"] for r in by_sym} == {"e1", "e3"}

    by_type = of_db.recent_events(event_type="block", db_path=db)
    assert {r["id"] for r in by_type} == {"e2", "e3"}

    by_prem = of_db.recent_events(min_premium=100_000, db_path=db)
    assert [r["id"] for r in by_prem] == ["e2"]

    since = of_db.recent_events(since="2026-07-09T00:00:00+00:00", db_path=db)
    assert {r["id"] for r in since} == {"e1", "e2"}
    assert since[0]["id"] == "e2"  # newest first

    for i in range(60):
        of_db.insert_event(
            _sample_event(
                id=f"bulk_{i}",
                ts=f"2026-07-01T{i % 24:02d}:00:00+00:00",
                premium_usd=1.0,
            ),
            db_path=db,
        )
    capped = of_db.recent_events(limit=100, db_path=db)
    assert len(capped) <= 50


def test_upsert_daily_idempotent_and_baselines(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    rows = [
        {
            "underlying": "NVDA",
            "occ_symbol": "NVDA260821C00190000",
            "date": "2026-07-01",
            "right": "call",
            "dte": 50,
            "dte_bucket": "31-90",
            "volume": 100,
            "prints": 5,
            "premium_usd": 10_000.0,
            "close_oi": 200,
        },
        {
            "underlying": "NVDA",
            "occ_symbol": "NVDA260821C00200000",
            "date": "2026-07-01",
            "right": "call",
            "dte": 50,
            "dte_bucket": "31-90",
            "volume": 50,
            "prints": 2,
            "premium_usd": 5_000.0,
            "close_oi": 100,
        },
        {
            "underlying": "NVDA",
            "occ_symbol": "NVDA260821C00190000",
            "date": "2026-07-02",
            "right": "call",
            "dte": 49,
            "dte_bucket": "31-90",
            "volume": 200,
            "prints": 8,
            "premium_usd": 20_000.0,
            "close_oi": 210,
        },
        {
            "underlying": "NVDA",
            "occ_symbol": "NVDA260821C00190000",
            "date": "2026-07-03",
            "right": "call",
            "dte": 48,
            "dte_bucket": "31-90",
            "volume": 300,
            "prints": 10,
            "premium_usd": 30_000.0,
            "close_oi": 220,
        },
    ]
    n1 = of_db.upsert_daily(rows, db_path=db)
    assert n1 == 4
    # Idempotent on PK — rewrite one row with new volume
    n2 = of_db.upsert_daily(
        [
            {
                "underlying": "NVDA",
                "occ_symbol": "NVDA260821C00190000",
                "date": "2026-07-03",
                "right": "call",
                "dte": 48,
                "dte_bucket": "31-90",
                "volume": 300,  # same day total still 300
                "prints": 11,
                "premium_usd": 31_000.0,
                "close_oi": 221,
            }
        ],
        db_path=db,
    )
    assert n2 == 1

    # Day vols: 2026-07-01 = 100+50=150, 07-02=200, 07-03=300 → mean=216.666…, n=3
    stats = of_db.daily_baselines("NVDA", "31-90", lookback_days=20, db_path=db)
    assert stats["n"] == 3
    day_vols = [150.0, 200.0, 300.0]
    expected_mean = sum(day_vols) / 3
    expected_std = math.sqrt(
        sum((v - expected_mean) ** 2 for v in day_vols) / 2
    )
    assert stats["mean"] == pytest.approx(expected_mean)
    assert stats["std"] == pytest.approx(expected_std)

    as_of = of_db.daily_baselines(
        "NVDA", "31-90", lookback_days=20, as_of="2026-07-02", db_path=db
    )
    assert as_of["n"] == 2
    assert as_of["mean"] == pytest.approx(175.0)


def test_stamp_returns_and_prune(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    of_db.insert_event(
        _sample_event(id="keep", ts="2026-07-01T12:00:00+00:00"),
        db_path=db,
    )
    of_db.insert_event(
        _sample_event(id="old", ts="2025-01-01T12:00:00+00:00"),
        db_path=db,
    )
    of_db.upsert_daily(
        [
            {
                "underlying": "NVDA",
                "occ_symbol": "NVDAKEEP",
                "date": "2026-06-01",
                "dte_bucket": "31-90",
                "volume": 10,
            },
            {
                "underlying": "NVDA",
                "occ_symbol": "NVDAOLD",
                "date": "2024-01-01",
                "dte_bucket": "31-90",
                "volume": 10,
            },
        ],
        db_path=db,
    )

    of_db.stamp_returns("keep", 0.01, -0.02, db_path=db)
    kept = of_db.recent_events(db_path=db)
    by_id = {r["id"]: r for r in kept}
    assert by_id["keep"]["ret_1d"] == pytest.approx(0.01)
    assert by_id["keep"]["ret_5d"] == pytest.approx(-0.02)

    of_db.prune("2026-07-09T00:00:00+00:00", db_path=db)
    after = of_db.recent_events(db_path=db)
    assert {r["id"] for r in after} == {"keep"}

    stats = of_db.daily_baselines("NVDA", "31-90", db_path=db)
    # only 2026-06-01 remains (2024-01-01 is >400 days before 2026-07-09)
    assert stats["n"] == 1
    assert stats["mean"] == pytest.approx(10.0)


# --- alpaca client ---------------------------------------------------------------


def _adapter() -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
        data_base_url="https://data.alpaca.markets",
        data_feed="opra",
    )


def test_get_option_bars_url_params_and_chunking(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_get_data(path: str, *, params: dict[str, Any] | None = None) -> dict:
        calls.append((path, dict(params) if params else None))
        assert path == "/v1beta1/options/bars"
        assert params is not None
        assert "symbols" in params
        assert params["timeframe"] == "1Day"
        assert params["start"] == "2024-02-12"
        syms = params["symbols"].split(",")
        assert len(syms) <= 100
        # no write/order endpoints
        assert "/orders" not in path
        assert "POST" not in path
        return {
            "bars": {
                syms[0]: [
                    {
                        "t": "2024-02-12T05:00:00Z",
                        "o": 1.0,
                        "h": 1.2,
                        "l": 0.9,
                        "c": 1.1,
                        "v": 10,
                        "n": 2,
                    }
                ]
            },
            "next_page_token": None,
        }

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)
    symbols = [f"OCC{i:03d}" for i in range(105)]
    out = adapter.get_option_bars(symbols, start="2024-02-12", timeframe="1Day")
    assert len(calls) == 2  # 100 + 5
    assert "OCC000" in out
    assert out["OCC000"][0]["c"] == 1.1
    assert out["OCC000"][0]["v"] == 10


def test_get_option_bars_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()

    def boom(path: str, *, params: dict[str, Any] | None = None) -> dict:
        raise RuntimeError("network down")

    monkeypatch.setattr(adapter, "_get_data", boom)
    assert adapter.get_option_bars(["AAPL240119C00100000"], start="2024-02-12") == {}


def test_get_option_contracts_trading_base(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(
        base: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict:
        calls.append((base, path, dict(params) if params else None))
        assert base == adapter._base_url
        assert path == "/v2/options/contracts"
        assert "/orders" not in path
        assert params is not None
        assert params["underlying_symbols"] == "SPY"
        assert params["limit"] == 1000
        return {
            "option_contracts": [
                {
                    "symbol": "SPY240119C00500000",
                    "open_interest": "6168",
                    "open_interest_date": "2024-01-12",
                    "strike_price": "500",
                    "expiration_date": "2024-01-19",
                    "type": "call",
                }
            ],
            "next_page_token": None,
        }

    monkeypatch.setattr(adapter, "_request", fake_request)
    contracts = adapter.get_option_contracts(
        "SPY", expiration_date_gte="2024-01-01", limit=1000
    )
    assert len(calls) == 1
    assert calls[0][2] is not None
    assert calls[0][2]["expiration_date_gte"] == "2024-01-01"
    assert len(contracts) == 1
    assert contracts[0]["open_interest"] == "6168"
    assert contracts[0]["type"] == "call"


def test_get_option_contracts_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()

    def boom(base: str, path: str, *, params: dict[str, Any] | None = None) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(adapter, "_request", boom)
    assert adapter.get_option_contracts("SPY") == []


def test_get_option_chain_snapshot_path(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_get_data(path: str, *, params: dict[str, Any] | None = None) -> dict:
        calls.append((path, dict(params) if params else None))
        assert path == "/v1beta1/options/snapshots/NVDA"
        assert params == {"feed": "opra"}
        assert "/orders" not in path
        return {
            "snapshots": {
                "NVDA260821C00190000": {"latestTrade": {"p": 1.5, "s": 10}},
            }
        }

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)
    snaps = adapter.get_option_chain_snapshot("NVDA")
    assert "NVDA260821C00190000" in snaps
    assert len(calls) == 1


def test_get_option_chain_snapshot_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()

    def boom(path: str, *, params: dict[str, Any] | None = None) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(adapter, "_get_data", boom)
    assert adapter.get_option_chain_snapshot("NVDA") == {}


def test_no_order_write_paths_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Safety: options-flow methods never touch order/write endpoints."""
    adapter = _adapter()
    seen_paths: list[str] = []

    def fake_get_data(path: str, *, params: dict[str, Any] | None = None) -> dict:
        seen_paths.append(path)
        return {"bars": {}, "snapshots": {}, "next_page_token": None}

    def fake_request(
        base: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict:
        seen_paths.append(path)
        return {"option_contracts": [], "next_page_token": None}

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)
    monkeypatch.setattr(adapter, "_request", fake_request)

    adapter.get_option_bars(["X"], start="2024-01-01")
    adapter.get_option_contracts("SPY")
    adapter.get_option_chain_snapshot("AAPL")

    forbidden = ("/orders", "/v2/orders", "/positions", "/watchlists")
    for path in seen_paths:
        for bad in forbidden:
            assert bad not in path
    assert all(
        p.startswith("/v1beta1/options/") or p == "/v2/options/contracts"
        for p in seen_paths
    )
