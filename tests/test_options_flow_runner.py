"""Hermetic tests for the extracted flow pipeline glue (runner + collector).

This glue lived untested in api/main.py before the extraction; these pin the
seams: session gating, poll-state fallback, print handoff, and the collector's
single-OPRA-connection interlock.
"""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

import signals.options_flow.runner as runner_mod
from signals.options_flow.poll import _default_state
from signals.options_flow.runner import OptionsFlowRunner


def _runner() -> OptionsFlowRunner:
    return OptionsFlowRunner(watchlist_symbols=lambda: ["AAPL"])


def test_subscription_set_empty_outside_regular_session(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "_market_session", lambda: "pre")
    # Pin the warm window OFF: "pre + inside the warm window" legitimately
    # serves actives (#71's OI pre-seed) — this test means "pre, before it".
    # Unpinned, the real clock made this fail on any 09:15–09:30 ET suite run.
    monkeypatch.setattr(runner_mod, "_in_preopen_warm", lambda *a: False)
    monkeypatch.setitem(_default_state.active, "AAPL260918C00200000", {"x": 1})
    try:
        assert _runner().subscription_set() == []
    finally:
        _default_state.active.pop("AAPL260918C00200000", None)


def test_subscription_set_returns_active_in_regular(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "_market_session", lambda: "regular")
    monkeypatch.setitem(_default_state.active, "AAPL260918C00200000", {"x": 1})
    try:
        assert "AAPL260918C00200000" in _runner().subscription_set()
    finally:
        _default_state.active.pop("AAPL260918C00200000", None)


def test_meta_for_prefers_poll_state_and_falls_back_to_occ_parse() -> None:
    r = _runner()
    occ = "AAPL260918C00200000"
    _default_state.active[occ] = {"underlying": "AAPL", "marker": "from-poll"}
    try:
        assert r.meta_for(occ)["marker"] == "from-poll"
    finally:
        _default_state.active.pop(occ, None)
    # Not in poll state: parsed from the OCC symbol itself.
    parsed = r.meta_for(occ)
    assert parsed is not None
    assert parsed.get("underlying") == "AAPL"


def test_on_prints_appends_to_stream_and_noops_without_stream() -> None:
    r = _runner()
    r.on_prints([{"p": 1}])  # no stream yet — must not raise

    class FakeStream:
        collected_prints: deque = deque()

    r.stream = FakeStream()
    r.on_prints([{"p": 1}, {"p": 2}])
    assert len(FakeStream.collected_prints) == 2


def test_collector_refuses_without_ownership_flag(monkeypatch) -> None:
    import tools.flow_collector as fc

    monkeypatch.delenv("OPTIONS_FLOW_EXTERNAL_COLLECTOR", raising=False)
    assert asyncio.run(fc.main()) == 2


def test_collector_refuses_without_pipeline_flags(monkeypatch) -> None:
    import tools.flow_collector as fc

    monkeypatch.setenv("OPTIONS_FLOW_EXTERNAL_COLLECTOR", "1")
    monkeypatch.delenv("OPTIONS_FLOW_ENABLED", raising=False)
    monkeypatch.delenv("OPTIONS_FLOW_STREAM_ENABLED", raising=False)
    assert asyncio.run(fc.main()) == 2


def test_collector_watchlist_symbols_uses_flow_list_pinned_first(monkeypatch) -> None:
    # The collector sources the dedicated "flow" list (pinned-first) via the
    # settings-cache-dropping fresh variant — 07-22 flow-list round.
    import signals.universe as universe
    import tools.flow_collector as fc

    monkeypatch.setattr(universe, "reload_settings", lambda: None)
    monkeypatch.setattr(
        universe,
        "get_watchlists_state",
        lambda: {
            "active_id": "flow",
            "lists": [
                {"id": "main", "symbols": ["ZZZT"]},
                {"id": "flow", "symbols": ["TSLA", "SPY", "AAPL"], "pinned": ["SPY"]},
            ],
        },
    )
    assert fc.watchlist_symbols() == ["SPY", "TSLA", "AAPL"]


@pytest.mark.parametrize("flag", ["0", "false", "no"])
def test_external_collector_flag_parsing_matches_api(flag, monkeypatch) -> None:
    # The interlock only works if both sides parse the flag the same way.
    import tools.flow_collector as fc

    monkeypatch.setenv("OPTIONS_FLOW_EXTERNAL_COLLECTOR", flag)
    assert fc._flag("OPTIONS_FLOW_EXTERNAL_COLLECTOR") is False
    monkeypatch.setenv("OPTIONS_FLOW_EXTERNAL_COLLECTOR", "1")
    assert fc._flag("OPTIONS_FLOW_EXTERNAL_COLLECTOR") is True
