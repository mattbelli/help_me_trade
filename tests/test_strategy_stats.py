"""Tests for the validated-strategy stats loader + conviction mapping."""

from __future__ import annotations

import json
from pathlib import Path

from signals.strategy_stats import (
    get_strategy_stats,
    load_strategy_stats,
    strategy_conviction,
)

_SAMPLE = {
    "as_of": "2026-06-26",
    "source": "Norgate survivorship-free S&P500 C&P 2010-2026",
    "strategies": {
        "dip_buy": {
            "label": "Dip-Buy",
            "win_rate": 0.558,
            "ev_pct": 0.59,
            "ev_ci_pct": [0.38, 1.02],
            "n_setups": 21462,
            "per_year_positive": "16/17",
            "validated": "validated",
            "method": "block-bootstrap + OOS",
            "note": "the dip edge",
        },
        "earnings_drift": {
            "label": "Earnings Drift",
            "win_rate": 0.572,
            "ev_pct": 0.89,
            "ev_ci_pct": None,
            "n_setups": 6624,
            "validated": "strong-unverified",
        },
    },
}


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "strategy_stats.json"
    p.write_text(json.dumps(_SAMPLE))
    return p


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert load_strategy_stats(missing) == {}
    assert get_strategy_stats("dip_buy", path=missing) is None
    assert strategy_conviction("dip_buy", path=missing) is None


def test_conviction_maps_validated_strategy(tmp_path: Path) -> None:
    p = _write(tmp_path)
    c = strategy_conviction("dip_buy", path=p)
    assert c is not None
    assert c["basis"] == "strategy_backtest"
    assert c["win_prob"] == 0.558  # back-compat alias for phone/UI
    assert c["win_rate"] == 0.558
    assert c["ev_pct"] == 0.59
    assert c["ev_ci_pct"] == [0.38, 1.02]
    assert c["n_setups"] == 21462
    assert c["validated"] == "validated"
    assert c["source"] == _SAMPLE["source"]
    assert c["as_of"] == "2026-06-26"


def test_conviction_strong_unverified_has_null_ci(tmp_path: Path) -> None:
    p = _write(tmp_path)
    c = strategy_conviction("earnings_drift", path=p)
    assert c is not None
    assert c["validated"] == "strong-unverified"
    assert c["ev_ci_pct"] is None  # never fabricate a CI before significance testing
    assert c["win_prob"] == 0.572


def test_unknown_strategy_returns_none(tmp_path: Path) -> None:
    p = _write(tmp_path)
    assert strategy_conviction("session_node_spy", path=p) is None
