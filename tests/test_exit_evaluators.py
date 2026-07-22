"""Pure exit evaluator tests — no DB, no broker, no fixtures."""

from __future__ import annotations

import pytest

from signals.exit_evaluators import evaluate_dip_exit, evaluate_earnings_exit, evaluate_vix_exit


def _raise(_d):
    raise RuntimeError("earnings_hour blew up")


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
    # Holiday-free span (2026-01-02 → 2026-01-16 has no NYSE closure between them) so the
    # count is an unambiguous 10 trading days out. Do NOT end on 2026-01-19 — that's MLK
    # Day, which the trading-day calendar correctly excludes (would be 9).
    d = evaluate_earnings_exit(
        today="2026-01-02",
        earnings_dates=["2025-10-15", "2026-01-16"],
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


# --- session-aware exit timing ---------------------------------------------------
# Print is Thu 2026-07-30, so Wed 07-29 is td == 1 and Tue 07-28 is td == 2. Stamps are
# production-shaped: `today` reaches the evaluator as UTC (`now_iso()`), and ET is -04:00
# in July, so any ET evening stamp carries the NEXT day's UTC date.
_DATES = ["2026-04-30", "2026-07-30"]
_TUE_2001 = "2026-07-29T00:01:00+00:00"  # Tue 20:01 ET, td == 2 — UTC date is already Wed
_WED_1100 = "2026-07-29T15:00:00+00:00"  # Wed 11:00 ET, td == 1, mid-session
_WED_1550 = "2026-07-29T19:50:00+00:00"  # Wed 15:50 ET, td == 1, inside the exit window
_WED_1630 = "2026-07-29T20:30:00+00:00"  # Wed 16:30 ET, td == 1, past the close
_THU_1000 = "2026-07-30T14:00:00+00:00"  # Thu 10:00 ET, td == 0, report day pre-close


def _earnings(today, **kwargs):
    return evaluate_earnings_exit(today=today, earnings_dates=_DATES, params={}, **kwargs)


def test_earnings_utc_rollover_does_not_exit_a_session_early():
    # REGRESSION: the ET date is Tue (td == 2) but the UTC date is already Wed. Reading
    # the UTC date exited here, a full session early, losing a day of drift every trade.
    d = _earnings(_TUE_2001, hour_for=lambda _d: "bmo")
    assert d.action == "hold"
    assert d.detail["td_to_earnings"] == 2
    assert d.detail["exit_rule"] == "hold_until_lead"


def test_earnings_report_day_exits_even_before_the_close():
    # REGRESSION: a strict `>` on the date filter dropped the print itself, so report day
    # yielded no earnings exit at all and the position rode through on max_holding_days.
    d = _earnings(_THU_1000, hour_for=lambda _d: "amc")
    assert d.action == "exit"
    assert d.reason == "earnings"
    assert d.exit_fraction == 1.0
    assert d.detail["td_to_earnings"] == 0
    assert d.detail["exit_rule"] == "report_day"


def test_earnings_report_day_exits_for_bmo_too():
    # The print already happened at the open — damage control, but never keep holding.
    d = _earnings(_THU_1000, hour_for=lambda _d: "bmo")
    assert d.action == "exit"
    assert d.detail["exit_rule"] == "report_day"


def test_earnings_amc_holds_through_final_session():
    d = _earnings(_WED_1100, hour_for=lambda _d: "amc")
    assert d.action == "hold"
    assert d.detail["td_to_earnings"] == 1
    # The hold must record the amc that caused it, not read as "hour unknown".
    assert d.detail["session"] == "amc"
    assert d.detail["exit_rule"] == "amc_after_close"


def test_earnings_amc_exits_after_close():
    # The day-TIF order queues here and releases at the report day's open — the gap trade.
    d = _earnings(_WED_1630, hour_for=lambda _d: "amc")
    assert d.action == "exit"
    assert d.reason == "earnings"
    assert d.exit_fraction == 1.0
    assert d.detail["session"] == "amc"
    assert d.detail["exit_rule"] == "amc_after_close"


def test_earnings_non_amc_holds_before_the_exit_window():
    d = _earnings(_WED_1100, hour_for=lambda _d: "bmo")
    assert d.action == "hold"
    assert d.detail["exit_rule"] == "lead_td_close"


def test_earnings_non_amc_exits_near_the_final_close():
    d = _earnings(_WED_1550, hour_for=lambda _d: "bmo")
    assert d.action == "exit"
    assert d.reason == "earnings"
    assert d.detail["exit_rule"] == "lead_td_close"


def test_earnings_non_amc_holds_after_the_close():
    # A day-TIF order submitted now would release at the report day's OPEN — after a bmo
    # print. Missing the window falls to the report-day backstop, never to a queued order.
    d = _earnings(_WED_1630, hour_for=lambda _d: "bmo")
    assert d.action == "hold"
    assert d.detail["exit_rule"] == "lead_td_close"


# --- fail closed: everything that is not exactly "amc" takes the non-amc path ---
@pytest.mark.parametrize(
    "hour_for",
    [
        pytest.param(lambda _d: "bmo", id="bmo"),
        pytest.param(lambda _d: "dmh", id="dmh"),
        pytest.param(lambda _d: None, id="unknown_hour"),
        pytest.param(lambda _d: "AMC", id="wrong_case"),
        pytest.param(_raise, id="hour_for_raises"),
    ],
)
def test_earnings_non_amc_never_holds_overnight(hour_for):
    # Holds mid-session (waiting for the window), exits in it, and never rides past the
    # close — the amc-only overnight hold must not leak to any other session.
    assert _earnings(_WED_1100, hour_for=hour_for).action == "hold"
    assert _earnings(_WED_1550, hour_for=hour_for).action == "exit"
    assert _earnings(_WED_1630, hour_for=hour_for).action == "hold"


def test_earnings_no_hour_for_takes_non_amc_path():
    d = _earnings(_WED_1550)
    assert d.action == "exit"
    assert d.detail["session"] is None
    assert d.detail["exit_rule"] == "lead_td_close"


def test_earnings_amc_naive_today_fails_closed():
    # A naive stamp has no knowable ET offset, so there is no clock to gate on -> take the
    # date-granularity exit rather than risk holding overnight on an unverifiable `amc`.
    d = evaluate_earnings_exit(
        today="2026-07-29", earnings_dates=_DATES, params={}, hour_for=lambda _d: "amc"
    )
    assert d.action == "exit"
    assert d.detail["exit_rule"] == "lead_td"


# --- the whole week, as the exit engine's 20s loop actually sees it (UTC stamps) ---
@pytest.mark.parametrize(
    ("stamp", "et", "amc_action", "non_amc_action"),
    [
        ("2026-07-28T15:00:00+00:00", "Tue 11:00", "hold", "hold"),
        ("2026-07-28T19:50:00+00:00", "Tue 15:50", "hold", "hold"),
        ("2026-07-29T00:01:00+00:00", "Tue 20:01", "hold", "hold"),
        ("2026-07-29T13:00:00+00:00", "Wed 09:00", "hold", "hold"),
        ("2026-07-29T15:00:00+00:00", "Wed 11:00", "hold", "hold"),
        ("2026-07-29T19:44:00+00:00", "Wed 15:44", "hold", "hold"),
        ("2026-07-29T19:50:00+00:00", "Wed 15:50", "hold", "exit"),
        ("2026-07-29T20:30:00+00:00", "Wed 16:30", "exit", "hold"),
        ("2026-07-30T00:01:00+00:00", "Wed 20:01", "exit", "hold"),
        ("2026-07-30T13:00:00+00:00", "Thu 09:00", "exit", "exit"),
        ("2026-07-30T14:00:00+00:00", "Thu 10:00", "exit", "exit"),
    ],
)
def test_earnings_week_timing_table(stamp, et, amc_action, non_amc_action):
    assert _earnings(stamp, hour_for=lambda _d: "amc").action == amc_action, et
    assert _earnings(stamp, hour_for=lambda _d: "bmo").action == non_amc_action, et


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


# --- half-day early close (board: market-hours-half-day-early-close) --------------
# 2026-11-27 (Friday after Thanksgiving) closes 13:00 ET; a bmo reporter prints
# Monday 2026-11-30, so Friday is td == 1. The fixed 15:45 window opened 2h45m
# AFTER the close: the exit never fired in-session, the day-TIF order queued to
# Monday's open — the print itself. The window is now session-relative (12:45).
# November ET is -05:00, so UTC = ET + 5h.
# A past distractor + the Monday print date (neither is itself a half-day; the
# half-day is "today", 11-27).
_EARNINGS_AROUND_HALF_DAY = ["2026-08-27", "2026-11-30"]
_FRI_1240 = "2026-11-27T17:40:00+00:00"  # 12:40 ET — before the 12:45 window
_FRI_1245 = "2026-11-27T17:45:00+00:00"  # 12:45 ET — exact window open (>=)
_FRI_1250 = "2026-11-27T17:50:00+00:00"  # 12:50 ET — inside the final 15 min
_FRI_1400 = "2026-11-27T19:00:00+00:00"  # 14:00 ET — the old phantom-open window


def _half_day(today):
    return evaluate_earnings_exit(
        today=today,
        earnings_dates=_EARNINGS_AROUND_HALF_DAY,
        params={},
        hour_for=lambda _d: "bmo",
    )


def test_earnings_half_day_holds_before_relative_window():
    d = _half_day(_FRI_1240)
    assert d.action == "hold"
    assert d.detail["exit_rule"] == "lead_td_close"


def test_earnings_half_day_exits_in_final_15_min_before_1300():
    d = _half_day(_FRI_1250)
    assert d.action == "exit"
    assert d.reason == "earnings"
    assert d.detail["exit_rule"] == "lead_td_close"


def test_earnings_half_day_window_opens_exactly_at_1245():
    assert _half_day(_FRI_1245).action == "exit"


def test_earnings_half_day_never_fires_in_the_closed_afternoon():
    # THE catastrophic case: 14:00 ET reads closed now, so no day-TIF order can
    # queue into Monday's open (the print). Missing the window falls to the
    # report-day backstop, never to a queued order through the announcement.
    d = _half_day(_FRI_1400)
    assert d.action == "hold"
    assert d.detail["exit_rule"] == "lead_td_close"
