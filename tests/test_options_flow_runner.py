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


def test_on_prints_appends_to_stream_and_noops_without_stream(monkeypatch) -> None:
    # Pin the calendar ON: unpinned, the real clock made this fail on any
    # weekend suite run once intake started dropping non-session prints.
    monkeypatch.setattr(runner_mod, "is_trading_day", lambda d: True)
    r = _runner()
    r.on_prints([{"p": 1}])  # no stream yet — must not raise

    class FakeStream:
        collected_prints: deque = deque()

    r.stream = FakeStream()
    r.on_prints([{"p": 1}, {"p": 2}])
    assert len(FakeStream.collected_prints) == 2


def test_on_prints_drops_batches_on_non_trading_days(monkeypatch) -> None:
    """OPRA runs Saturday industry tests over the PRODUCTION feed — the
    2026-08-08 dynamic-rebalance test streamed 421k messages into the standing
    session and minted 2,421 fake events. A print arriving on a non-session
    date is never real: intake must drop it so every detector downstream
    (blocks, bursts, clusters, sweeps, print lab, gauges) starves."""
    monkeypatch.setattr(runner_mod, "is_trading_day", lambda d: False)
    r = _runner()

    class FakeStream:
        collected_prints: deque = deque()

    r.stream = FakeStream()
    r.on_prints([{"p": 1}, {"p": 2}])
    assert len(FakeStream.collected_prints) == 0


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


def test_collector_rust_spine_refuses_without_binary(monkeypatch, tmp_path) -> None:
    """OPTIONS_FLOW_RUST_SPINE=1 supervises collectors/flow_rs; a missing binary
    is a loud exit (systemd would restart-loop, but the log names the fix)."""
    import tools.flow_collector as fc

    for k in ("OPTIONS_FLOW_EXTERNAL_COLLECTOR", "OPTIONS_FLOW_ENABLED",
              "OPTIONS_FLOW_STREAM_ENABLED", "OPTIONS_FLOW_RUST_SPINE"):
        monkeypatch.setenv(k, "1")
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    monkeypatch.setattr(fc, "FLOW_RS_BIN", tmp_path / "flow_rs")
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
