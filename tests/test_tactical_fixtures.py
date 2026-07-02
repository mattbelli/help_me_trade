"""Golden-fixture validation for the tactical sleeves (PC PR #27 — momentum-Gate-1 style).

Runs the REAL Phase-1 (steep-YC gate) and Phase-2 (VIX-spike entry/exit) decision logic over
the PC's reference FRED series and asserts it reproduces the known-correct outputs bit-exact.
`is_vix_spike_entry` + `evaluate_vix_exit` are the SAME functions the live scan / exit engine
call; the YC gate mirrors `signals.regime.market_regime`'s term_spread = DGS10 - DGS2 > threshold.

Fixtures: research/fixtures/{tactical_regime_series,vix_spike_triggers,steep_yc_gate}.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.strategy_features import is_vix_spike_entry
from signals.exit_evaluators import evaluate_vix_exit

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "tactical_regime_series.json").exists(),
    reason="tactical fixtures absent (ship via PR #27)",
)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _series():
    return _load("tactical_regime_series.json")["series"]


def _exit_reason(decision) -> str | None:
    return {"time_cap": "60d_max_hold", "vix_reversion": "vix<20"}.get(decision.reason)


def test_vix_spike_reproduces_14_golden_episodes() -> None:
    # State machine over the VIX trading calendar: enter when flat and the predicate fires,
    # then walk forward to the exit. "No open position" is enforced by the loop (jump past exit).
    vix = [(r["date"], r["vix"]) for r in _series()]
    golden = _load("vix_spike_triggers.json")["episodes"]
    n = len(vix)

    episodes: list[tuple[str, str, str | None, int]] = []
    i = 0
    while i < n:
        entry_date, entry_vix = vix[i]
        prior = [v for _, v in vix[i - 12 : i]] if i >= 12 else []
        if is_vix_spike_entry(entry_vix, prior):
            entry_i = i
            j = i
            while j < n:
                decision = evaluate_vix_exit(
                    current_vix=vix[j][1], holding_days=j - entry_i, params={}
                )
                if decision.action == "exit":
                    episodes.append(
                        (entry_date, vix[j][0], _exit_reason(decision), j - entry_i)
                    )
                    i = j + 1
                    break
                j += 1
            else:
                i = n
        else:
            i += 1

    expected = [
        (e["entry_date"], e["exit_date"], e["exit_reason"], e["hold_trading_days"])
        for e in golden
    ]
    assert episodes == expected


def test_steep_yc_gate_reproduces_golden_days_and_transitions() -> None:
    series = _series()
    gate = _load("steep_yc_gate.json")

    # term_spread = DGS10 - DGS2 (unrounded, exactly like market_regime); strict > 0.5
    # (the steepyc_dip seed default). Missing data ⇒ fail-closed (not steep).
    def spread(row):
        y10, y2 = row.get("y10"), row.get("y2")
        return None if y10 is None or y2 is None else y10 - y2

    steep_days = {
        r["date"] for r in series if (s := spread(r)) is not None and s > 0.5
    }
    assert steep_days == set(gate["steep_days"])

    rows = sorted((r for r in series if spread(r) is not None), key=lambda r: r["date"])
    transitions: list[dict[str, str]] = []
    prev = False
    for r in rows:
        cur = spread(r) > 0.5
        if cur != prev:
            transitions.append({"date": r["date"], "gate": "OPEN" if cur else "CLOSE"})
        prev = cur
    assert transitions == gate["gate_transitions"]
