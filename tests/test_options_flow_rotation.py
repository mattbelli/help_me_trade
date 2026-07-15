"""Quote rotation v2 — anchor budget, dollar-volume selection, pre-open warm.

Pins the 2026-07-15 re-derivation: Alpaca's quote cap measured at exactly 1,000
(405 at 2,000) with delivery ~14.4 quotes/s/contract (~0.09 cores at 1,000), so the
old 60-sub reactive-only pool was rationing against a constraint that didn't exist.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from signals.options_flow.constants import (
    ACTIVE_SET_LONG_DTE_MIN,
    HOT_QUOTE_MAX,
    LONG_DTE_THRESHOLD_DAYS,
    OPRA_QUOTE_SUB_CAP,
    QUOTE_ANCHOR_BUDGET,
)
from signals.options_flow.poll import (
    PollState,
    _day_dollar_volume,
    _select_active_picks,
    _skip_index_zero_dte,
    anchor_quote_symbols,
)
from signals.options_flow.runner import _in_preopen_warm
from signals.options_flow.stream import OptionsFlowStream
from trading.market_hours import _ET


def test_budgets_fit_inside_the_measured_vendor_cap():
    assert QUOTE_ANCHOR_BUDGET + HOT_QUOTE_MAX <= OPRA_QUOTE_SUB_CAP


# --- selection score -----------------------------------------------------------------


def test_dollar_volume_outranks_recency():
    """The old key was (ts_ms, size): a 1-lot from 2s ago beat a $2M block from
    3min ago. Dollar volume must invert that."""
    recent_tiny = _day_dollar_volume(
        {"dailyBar": {"v": 10}}, {"p": 0.05, "s": 1, "t": "2026-07-15T13:59:59Z"}
    )
    older_block = _day_dollar_volume(
        {"dailyBar": {"v": 40_000}}, {"p": 5.0, "s": 400, "t": "2026-07-15T13:31:00Z"}
    )
    assert older_block > recent_tiny


def test_dollar_volume_falls_back_to_trade_notional_at_the_bell():
    # dailyBar absent right at the open — the last trade's notional stands in.
    assert _day_dollar_volume({}, {"p": 2.0, "s": 50}) == pytest.approx(100.0)
    assert _day_dollar_volume({"dailyBar": {}}, {"p": 2.0, "s": 50}) == pytest.approx(100.0)


def test_index_megacap_zero_dte_skipped_at_selection():
    """ZERO_DTE_EXCLUDE discards these prints downstream — measured 07-15: 57-60%
    of SPY/QQQ's slots sat on contracts whose prints we threw away."""
    assert _skip_index_zero_dte("SPY", 0) is True
    assert _skip_index_zero_dte("QQQ", 1) is True
    assert _skip_index_zero_dte("SPY", 2) is False
    assert _skip_index_zero_dte("NVDA", 0) is False  # single names keep 0DTE


def test_long_dated_floor_swaps_in_leaps():
    """07-14 tape: 65% of >=100k premium lives at DTE>=90; a pure $vol rank still
    skews short — the floor must hold when long-dated supply exists."""
    short = [(1000.0 - i, 5, f"SHORT{i:03d}") for i in range(60)]
    long_dated = [(1.0, LONG_DTE_THRESHOLD_DAYS + 30, f"LEAP{i:03d}") for i in range(20)]
    picks = _select_active_picks(short + long_dated, 60)
    assert len(picks) == 60
    n_long = sum(1 for _, dte, _ in picks if dte >= LONG_DTE_THRESHOLD_DAYS)
    assert n_long >= ACTIVE_SET_LONG_DTE_MIN
    # the floor evicts the LOWEST-scored short picks, not the winners
    assert picks[0][2] == "SHORT000"


def test_long_dated_floor_is_supply_limited_not_fabricated():
    short = [(100.0 - i, 3, f"S{i:02d}") for i in range(30)]
    picks = _select_active_picks(short, 20)
    assert len(picks) == 20
    assert all(dte < LONG_DTE_THRESHOLD_DAYS for _, dte, _ in picks)


# --- anchors -------------------------------------------------------------------------


def _state_with(active: dict) -> PollState:
    st = PollState()
    st.active = active
    return st


def test_anchor_quotes_rank_by_score_and_clip():
    active = {
        f"OCC{i:04d}": {"rank_score": float(i)} for i in range(QUOTE_ANCHOR_BUDGET + 50)
    }
    anchors = anchor_quote_symbols(_state_with(active))
    assert len(anchors) == QUOTE_ANCHOR_BUDGET
    assert anchors[0] == f"OCC{QUOTE_ANCHOR_BUDGET + 49:04d}"  # highest score first
    assert f"OCC{0:04d}" not in anchors  # lowest fell off


def test_anchor_quotes_survive_missing_scores():
    # OI-seed path pre-open: metas may carry no rank_score at all.
    anchors = anchor_quote_symbols(_state_with({"AAA": {}, "BBB": {"rank_score": 5.0}}))
    assert anchors[0] == "BBB"
    assert "AAA" in anchors


# --- stream quote budget -------------------------------------------------------------


def _stream(anchors: list[str] | None) -> OptionsFlowStream:
    return OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda batch: None,
        get_subscription_set=lambda: [],
        meta_for=lambda occ: None,
        get_anchor_quotes=(lambda: anchors) if anchors is not None else None,
    )


def test_desired_quotes_unions_anchors_and_hot_pool():
    s = _stream(["ANCHOR1", "ANCHOR2"])
    now_ms = 1_000_000
    s._hot_print_ms = {"HOT1": now_ms}
    desired = s._desired_quotes(now_ms)
    assert desired == {"ANCHOR1", "ANCHOR2", "HOT1"}


def test_anchors_keep_slots_hot_pool_is_what_gets_trimmed():
    now_ms = 1_000_000
    anchors = [f"A{i:04d}" for i in range(QUOTE_ANCHOR_BUDGET)]
    s = _stream(anchors)
    s._hot_print_ms = {f"H{i:04d}": now_ms - i for i in range(HOT_QUOTE_MAX + 200)}
    desired = s._desired_quotes(now_ms)
    assert len(desired) <= OPRA_QUOTE_SUB_CAP
    assert all(a in desired for a in anchors)  # every anchor survived
    assert "H0000" in desired  # most recent hot survived
    assert f"H{HOT_QUOTE_MAX + 199:04d}" not in desired  # oldest hot trimmed


def test_pending_escalations_join_the_recency_ranking():
    s = _stream([])
    now_ms = 1_000_000
    s._hot_print_ms = {}
    desired = s._desired_quotes(now_ms, extra_hot={"NEWHOT"})
    assert desired == {"NEWHOT"}


def test_no_anchor_getter_behaves_like_before():
    s = _stream(None)
    now_ms = 1_000_000
    s._hot_print_ms = {"HOT1": now_ms}
    assert s._desired_quotes(now_ms) == {"HOT1"}


# --- pre-open warm window ------------------------------------------------------------


def _et(hour: int, minute: int) -> datetime:
    # Tue 2026-07-14, a regular session day
    return datetime(2026, 7, 14, hour, minute, tzinfo=_ET)


def test_preopen_warm_window():
    assert _in_preopen_warm(_et(9, 25)) is True  # 5 min out
    assert _in_preopen_warm(_et(9, 21)) is True  # 9 min out
    assert _in_preopen_warm(_et(9, 15)) is False  # too early
    assert _in_preopen_warm(_et(9, 30)) is False  # bell itself -> regular session's job
    assert _in_preopen_warm(_et(10, 0)) is False
