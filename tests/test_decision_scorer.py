"""Tests for live decision scorer and proposal score attachment."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db import base as db_base
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from research.dataset_io import feature_names, featurize_dict
from signals.decision_scorer import load_artifacts, score_features, score_symbol
from trading.proposals import create_phone_proposal


def _synthetic_preprocessing() -> dict:
    names = feature_names()
    numeric = [n for n in names if not n.startswith("entry_context_") and not n.startswith("vs_sma")]
    medians = {n: 0.0 for n in numeric}
    means = {n: 0.0 for n in numeric}
    stds = {n: 1.0 for n in numeric}
    return {
        "numeric_features": numeric,
        "medians": medians,
        "means": means,
        "stds": stds,
        "entry_context_categories": [
            "pullback",
            "dip_below_trend",
            "breakout",
            "momentum",
            "neutral",
        ],
    }


def _write_synthetic_artifacts(tmp_dir: Path) -> dict:
    preprocessing = _synthetic_preprocessing()
    names = feature_names()
    coefficients = {n: 0.0 for n in names}
    coefficients["rsi_14"] = 2.0
    coefficients["prior_return_3d"] = -1.5
    coefficients["entry_context_breakout"] = 0.8

    model = {
        "intercept": 0.0,
        "coefficients": coefficients,
        "feature_names": names,
        "preprocessing": preprocessing,
    }
    calibrator = {"A": 1.0, "B": 0.0}
    ev_table = [
        {
            "bin_lo": 0.0,
            "bin_hi": 0.5,
            "n": 40,
            "calibrated_winrate": 0.35,
            "empirical_winrate": 0.4,
            "wilson_ci_lo": 0.25,
            "wilson_ci_hi": 0.55,
            "avg_win_return": 3.0,
            "avg_loss_return": -2.0,
            "ev": 0.5,
            "sufficient": True,
            "flag": None,
        },
        {
            "bin_lo": 0.5,
            "bin_hi": 1.0,
            "n": 50,
            "calibrated_winrate": 0.65,
            "empirical_winrate": 0.6,
            "wilson_ci_lo": 0.45,
            "wilson_ci_hi": 0.75,
            "avg_win_return": 4.0,
            "avg_loss_return": -1.5,
            "ev": 1.2,
            "sufficient": True,
            "flag": None,
        },
    ]
    meta = {
        "base_rate": 0.5,
        "brier": 0.22,
        "n_samples": 90,
        "data_source": "test.db",
        "honesty": {"low_confidence": False},
    }

    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.joinpath("model.json").write_text(json.dumps(model))
    tmp_dir.joinpath("calibrator.json").write_text(json.dumps(calibrator))
    tmp_dir.joinpath("ev_table.json").write_text(json.dumps(ev_table))
    tmp_dir.joinpath("meta.json").write_text(json.dumps(meta))

    return {
        "model": model,
        "calibrator": calibrator,
        "ev_table": ev_table,
        "meta": meta,
    }


def _sample_features(*, rsi: float = 1.0, prior_return_3d: float = 0.5) -> dict:
    return {
        "rsi_14": rsi,
        "atr_14": 1.0,
        "dist_from_sma50_pct": 0.0,
        "dist_from_sma200_pct": 0.0,
        "dist_from_52w_high_pct": 0.0,
        "prior_return_1d": 0.0,
        "prior_return_3d": prior_return_3d,
        "prior_return_5d": 0.0,
        "pct_change_1d": 0.0,
        "pct_change_5d": 0.0,
        "pct_change_21d": 0.0,
        "vol_vs_avg_20": 0.0,
        "gap_prior": 0.0,
        "dist_from_high_252": 0.0,
        "rel_strength_21": 0.0,
        "entry_context": "breakout",
        "vs_sma50": "above",
        "vs_sma200": "above",
    }


def test_score_features_win_prob_bucket_and_top_factors(tmp_path: Path) -> None:
    artifacts = _write_synthetic_artifacts(tmp_path)
    features = _sample_features(rsi=2.0, prior_return_3d=1.0)

    result = score_features(features, artifacts)

    assert 0.0 <= result["win_prob"] <= 1.0
    assert result["ev_pct"] == 1.2
    assert result["ci_low"] == 0.45
    assert result["ci_high"] == 0.75
    assert result["sufficient"] is True
    assert result["base_rate"] == 0.5
    assert result["low_confidence"] is False
    assert result["n_samples"] == 90
    assert result["data_source"] == "test.db"

    names = [f["name"] for f in result["top_factors"]]
    assert names[0] == "rsi_14"
    assert result["top_factors"][0]["direction"] == "bullish"
    assert result["top_factors"][0]["contribution"] == pytest.approx(4.0)

    x = featurize_dict(features, artifacts["model"]["preprocessing"])
    expected = []
    for name in artifacts["model"]["feature_names"]:
        coef = artifacts["model"]["coefficients"][name]
        idx = artifacts["model"]["feature_names"].index(name)
        contribution = coef * float(x[idx])
        if contribution != 0.0:
            expected.append((name, abs(contribution)))
    expected.sort(key=lambda item: item[1], reverse=True)
    actual = [(f["name"], abs(f["contribution"])) for f in result["top_factors"]]
    assert actual == [(n, c) for n, c in expected[: len(actual)]]


def test_load_artifacts_missing_file_returns_none(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.joinpath("model.json").write_text("{}")
    assert load_artifacts(tmp_path) is None


def test_load_artifacts_caches_by_mtime(tmp_path: Path) -> None:
    _write_synthetic_artifacts(tmp_path)
    first = load_artifacts(tmp_path)
    second = load_artifacts(tmp_path)
    assert first is second


def test_score_symbol_buy_returns_score(monkeypatch, tmp_path: Path) -> None:
    artifacts = _write_synthetic_artifacts(tmp_path)
    features = _sample_features()

    monkeypatch.setattr(
        "signals.decision_scorer.load_artifacts",
        lambda _dir=tmp_path: artifacts,
    )
    monkeypatch.setattr(
        "signals.decision_scorer.get_daily_bars",
        lambda symbol, start, end, **kwargs: [{"d": "2024-01-01"}] * 300,
    )
    monkeypatch.setattr(
        "signals.decision_scorer.compute_features",
        lambda bars, idx, **kwargs: features,
    )

    result = score_symbol("AAPL", side="buy", artifact_dir=tmp_path)
    assert result is not None
    assert "win_prob" in result


def test_score_symbol_sell_returns_none(tmp_path: Path) -> None:
    assert score_symbol("AAPL", side="sell", artifact_dir=tmp_path) is None


def test_score_symbol_internal_error_returns_none(monkeypatch, tmp_path: Path) -> None:
    _write_synthetic_artifacts(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("bar fetch failed")

    monkeypatch.setattr("signals.decision_scorer.get_daily_bars", _boom)
    assert score_symbol("AAPL", side="buy", artifact_dir=tmp_path) is None


def _patch_phone_env(monkeypatch, db_path: Path) -> None:
    from brokers.alpaca import AlpacaAdapter
    from brokers.base import Account, Position

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db_path)

    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            qty=10.0,
            side="long",
            market_value=1_500.0,
            unrealized_pl=50.0,
            avg_entry_price=145.0,
            current_price=150.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db_path)

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _name: stub)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            "execution_mode": "dry_run",
            "kill_switch_active": False,
            "max_order_value_usd": 50_000,
            "max_position_pct": 30,
            "max_open_orders": 10,
            "max_daily_orders": 20,
        },
    )
    monkeypatch.setattr(
        "trading.proposals.notify",
        AsyncMock(return_value={}),
    )


@pytest.mark.asyncio
async def test_create_phone_proposal_stores_score_roundtrip(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "proposals.db"
    _patch_phone_env(monkeypatch, db)

    fixed_score = {
        "win_prob": 0.61,
        "ev_pct": 1.25,
        "ci_low": 0.45,
        "ci_high": 0.72,
        "sufficient": True,
        "base_rate": 0.5,
        "low_confidence": False,
        "brier": 0.22,
        "n_samples": 90,
        "data_source": "test.db",
        "top_factors": [{"name": "rsi_14", "direction": "bullish", "contribution": 0.4}],
    }
    monkeypatch.setattr(
        "signals.decision_scorer.score_symbol",
        lambda symbol, **kwargs: fixed_score,
    )

    row = await create_phone_proposal(
        {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"},
        ttl_seconds=300,
        db_path=db,
    )

    assert row["score"] == fixed_score

    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["score"] == fixed_score

    pending = paper_orders_mod.list_pending_proposals(db_path=db)
    assert len(pending) == 1
    assert pending[0]["score"] == fixed_score


@pytest.mark.asyncio
async def test_create_phone_proposal_none_score_stores_cleanly(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "proposals.db"
    _patch_phone_env(monkeypatch, db)
    monkeypatch.setattr(
        "signals.decision_scorer.score_symbol",
        lambda symbol, **kwargs: None,
    )

    row = await create_phone_proposal(
        {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"},
        ttl_seconds=300,
        db_path=db,
    )

    assert row["score"] is None
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["score"] is None
