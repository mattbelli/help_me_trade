"""Pure exit evaluator tests — no DB, no broker, no fixtures."""

from __future__ import annotations

from signals.exit_evaluators import evaluate_dip_exit, evaluate_earnings_exit, evaluate_vix_exit


def _dip(**kwargs):
    defaults = {
        "entry_price": 100.0,
        "current_price": 100.0,
        "high_water_mark": None,
        "first_target_hit": False,
        "holding_days": 5,
        "params": {},
    }
    defaults.update(kwargs)
    return evaluate_dip_exit(**defaults)


def test_dip_hold_phase1():
    d = _dip(current_price=101.0, bar_high=101.0, bar_low=101.0)
    assert d.action == "hold"
    assert d.reason == "hold"
    assert d.exit_fraction == 0.0


def test_dip_stop_phase1():
    d = _dip(bar_low=96.9, bar_high=100.0, current_price=97.0)
    assert d.action == "exit"
    assert d.reason == "stop"
    assert d.exit_fraction == 1.0


def test_dip_first_target_scale():
    d = _dip(bar_high=103.5, bar_low=100.0, current_price=103.0)
    assert d.action == "scale"
    assert d.reason == "first_target"
    assert d.exit_fraction == 0.5
    assert d.new_first_target_hit is True
    assert d.new_high_water_mark == 103.5


def test_dip_stop_beats_first_target_same_bar():
    d = _dip(bar_low=96.9, bar_high=103.5, current_price=100.0)
    assert d.action == "exit"
    assert d.reason == "stop"


def test_dip_phase2_trail_exit():
    d = _dip(
        first_target_hit=True,
        high_water_mark=120.0,
        bar_high=118.0,
        bar_low=106.0,
        current_price=107.0,
    )
    assert d.action == "exit"
    assert d.reason == "trail"
    assert d.exit_fraction == 1.0
    assert d.detail["effective_stop"] == 120.0 * (1 - 11.0 / 100)


def test_dip_phase2_breakeven_exit():
    d = _dip(
        first_target_hit=True,
        high_water_mark=105.0,
        bar_high=101.0,
        bar_low=99.0,
        current_price=100.0,
    )
    assert d.action == "exit"
    assert d.reason == "breakeven"
    assert d.detail["effective_stop"] == 100.0


def test_dip_phase2_hold_advances_hwm():
    d = _dip(
        first_target_hit=True,
        high_water_mark=110.0,
        bar_high=115.0,
        bar_low=112.0,
        current_price=114.0,
    )
    assert d.action == "hold"
    assert d.reason == "hold"
    assert d.new_high_water_mark == 115.0


def test_dip_time_cap():
    d = _dip(holding_days=20, current_price=101.0, bar_high=101.0, bar_low=101.0)
    assert d.action == "exit"
    assert d.reason == "time_cap"
    assert d.exit_fraction == 1.0


def test_dip_bad_entry_price():
    d = _dip(entry_price=0.0)
    assert d.action == "hold"
    assert d.detail.get("error") == "bad_entry_price"


def test_dip_fixed_hold_only_exits_at_max_holding():
    fixed_hold_params = {
        "stop_pct": None,
        "first_target_pct": None,
        "trail_pct": None,
        "max_holding_days": 10,
    }
    d = _dip(
        holding_days=5,
        bar_low=90.0,
        bar_high=110.0,
        current_price=100.0,
        params=fixed_hold_params,
    )
    assert d.action == "hold"
    assert d.reason == "hold"

    d = _dip(
        holding_days=10,
        current_price=101.0,
        bar_high=101.0,
        bar_low=101.0,
        params=fixed_hold_params,
    )
    assert d.action == "exit"
    assert d.reason == "time_cap"
    assert d.exit_fraction == 1.0


def test_dip_bar_high_low_default_to_current_price():
    d = _dip(current_price=96.9)
    assert d.action == "exit"
    assert d.reason == "stop"


def test_earnings_exit_within_lead():
    # Tuesday -> Wednesday is 1 trading day (exit_lead_td default 1).
    d = evaluate_earnings_exit(
        today="2026-01-06",
        earnings_dates=["2025-10-15", "2026-01-07"],
        params={},
    )
    assert d.action == "exit"
    assert d.reason == "earnings"
    assert d.exit_fraction == 1.0
    assert d.detail["next_earnings"] == "2026-01-07"
    assert d.detail["td_to_earnings"] == 1


def test_earnings_hold_when_far():
    d = evaluate_earnings_exit(
        today="2026-01-05",
        earnings_dates=["2025-10-15", "2026-01-19"],
        params={},
    )
    assert d.action == "hold"
    assert d.detail["td_to_earnings"] == 10


def test_earnings_no_future_date():
    d = evaluate_earnings_exit(
        today="2026-01-05",
        earnings_dates=["2025-12-01", "2025-12-15"],
        params={},
    )
    assert d.action == "hold"
    assert d.detail["next_earnings"] is None


def test_earnings_safety_cap():
    d = evaluate_earnings_exit(
        today="2026-01-05",
        earnings_dates=["2025-10-15", "2026-06-01"],
        holding_days=25,
        params={"max_holding_days": 20},
    )
    assert d.action == "exit"
    assert d.reason == "time_cap"
    assert d.exit_fraction == 1.0


def test_vix_exit_time_cap_precedence():
    d = evaluate_vix_exit(current_vix=35.0, holding_days=60, params={})
    assert d.action == "exit"
    assert d.reason == "time_cap"
    assert d.exit_fraction == 1.0


def test_vix_exit_reversion():
    d = evaluate_vix_exit(current_vix=19.0, holding_days=10, params={})
    assert d.action == "exit"
    assert d.reason == "vix_reversion"
    assert d.exit_fraction == 1.0


def test_vix_exit_hold_when_vix_high():
    d = evaluate_vix_exit(current_vix=25.0, holding_days=10, params={})
    assert d.action == "hold"
    assert d.reason == "hold"


def test_vix_exit_hold_when_vix_missing():
    d = evaluate_vix_exit(current_vix=None, holding_days=10, params={})
    assert d.action == "hold"
    assert d.detail.get("error") == "no_vix"


def test_vix_exit_boundary_vix_exactly_20_holds():
    d = evaluate_vix_exit(current_vix=20.0, holding_days=10, params={})
    assert d.action == "hold"
    assert d.reason == "hold"
