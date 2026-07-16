"""Hermetic tests for options-flow intraday poll cycle (fake adapter, tmp db)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from db import options_flow as of_db
from signals.options_flow import constants as C
from signals.options_flow.intraday import _ts_ms_to_iso
from signals.options_flow.poll import (
    PollState,
    _append_day_prints,
    run_poll_once,
)

TODAY = "2026-07-09"
EXPIRY = "2026-08-21"
OCC = "NVDA260821C00190000"
OCC2 = "NVDA260821C00200000"
NOW_MS = 1_752_000_000_000  # arbitrary fixed clock


def _contract(
    symbol: str,
    *,
    strike: float = 190.0,
    right: str = "call",
    oi: Any = 400,
    underlying: str = "NVDA",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "underlying_symbol": underlying,
        "strike_price": strike,
        "expiration_date": EXPIRY,
        "type": right,
        "open_interest": oi,
        "open_interest_date": TODAY,
    }


def _snapshot_trade(*, ts: str, size: int = 10) -> dict[str, Any]:
    return {"latestTrade": {"t": ts, "s": size, "p": 1.0, "x": "C", "c": ["I"]}}


def _trade(*, ts: str, price: float = 4.5, size: int = 400, conditions: list[str] | None = None) -> dict:
    return {
        "t": ts,
        "p": price,
        "s": size,
        "x": "C",
        "c": conditions if conditions is not None else ["I"],
    }


def _bar(*, v: int, c: float) -> dict[str, Any]:
    return {"t": f"{TODAY}T00:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": v, "n": 1}


class FakeAdapter:
    """Canned options-flow adapter for poll tests."""

    def __init__(
        self,
        *,
        contracts_by_u: dict[str, list[dict]] | None = None,
        snapshots_by_u: dict[str, dict[str, dict]] | None = None,
        snapshots_by_u_exp: dict[tuple[str, str], dict[str, dict]] | None = None,
        trades_by_occ: dict[str, list[dict]] | None = None,
        quotes_by_occ: dict[str, dict] | None = None,
        bars_by_occ: dict[str, list[dict]] | None = None,
        conditions: dict[str, str] | None = None,
        raise_snapshot_on: set[str] | None = None,
    ) -> None:
        self.contracts_by_u = contracts_by_u or {}
        self.snapshots_by_u = snapshots_by_u or {}
        self.snapshots_by_u_exp = snapshots_by_u_exp or {}
        self.trades_by_occ = trades_by_occ or {}
        self.quotes_by_occ = quotes_by_occ or {}
        self.bars_by_occ = bars_by_occ or {}
        self.conditions = conditions or {"I": "electronic"}
        self.raise_snapshot_on = {s.upper() for s in (raise_snapshot_on or set())}
        self.trades_calls: list[tuple[list[str], str]] = []
        self.chain_calls: list[tuple[str, int | None, int]] = []

    def get_option_trade_conditions(self) -> dict[str, str]:
        return dict(self.conditions)

    def get_option_contracts(
        self,
        underlying: str,
        *,
        expiration_date_gte: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        del expiration_date_gte, limit
        return list(self.contracts_by_u.get(underlying.strip().upper(), []))

    def get_option_chain_snapshot(
        self,
        underlying: str,
        feed: str = "opra",
        *,
        expiration_date: str | None = None,
        limit: int | None = None,
        max_pages: int = 1,
    ) -> dict:
        del feed
        u = underlying.strip().upper()
        self.chain_calls.append((u, limit, max_pages))
        if u in self.raise_snapshot_on:
            raise RuntimeError(f"boom:{u}")
        if expiration_date is not None:
            return dict(self.snapshots_by_u_exp.get((u, expiration_date), {}))
        return dict(self.snapshots_by_u.get(u, {}))

    def get_option_trades(
        self,
        occ_symbols: list[str],
        *,
        start: str,
        end: str | None = None,
        limit: int = 10_000,
    ) -> dict[str, list[dict]]:
        del end, limit
        self.trades_calls.append((list(occ_symbols), start))
        out: dict[str, list[dict]] = {}
        for occ in occ_symbols:
            key = occ.strip().upper()
            if key in self.trades_by_occ:
                out[key] = list(self.trades_by_occ[key])
        return out

    def get_option_latest_quotes(self, occ_symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for occ in occ_symbols:
            key = occ.strip().upper()
            if key in self.quotes_by_occ:
                out[key] = dict(self.quotes_by_occ[key])
        return out

    def get_option_bars(
        self,
        occ_symbols: list[str],
        start: str,
        end: str | None = None,
        timeframe: str = "1Day",
    ) -> dict[str, list[dict]]:
        del start, end, timeframe
        out: dict[str, list[dict]] = {}
        for occ in occ_symbols:
            key = occ.strip().upper()
            if key in self.bars_by_occ:
                out[key] = self.bars_by_occ[key]
        return out


def _base_adapter(**kwargs: Any) -> FakeAdapter:
    defaults = {
        "contracts_by_u": {"NVDA": [_contract(OCC)]},
        "snapshots_by_u": {
            "NVDA": {OCC: _snapshot_trade(ts=f"{TODAY}T14:00:00Z", size=50)},
        },
        "trades_by_occ": {},
        "quotes_by_occ": {},
        "bars_by_occ": {},
    }
    defaults.update(kwargs)
    return FakeAdapter(**defaults)


# --- state / rollover -------------------------------------------------------------


def test_day_rollover_resets_state() -> None:
    st = PollState(
        day="2026-07-08",
        cursors={OCC: f"{TODAY}T14:00:00Z"},
        oi={OCC: 400},
        oi_day="2026-07-08",
        last_active_refresh_ms=123,
        day_prints={"NVDA": [{"size": 1}]},
    )
    adapter = _base_adapter()
    run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
    )
    assert st.day == TODAY
    assert st.cursors == {}
    assert st.oi_day == TODAY
    assert st.oi.get(OCC) == 400  # repopulated on OI refresh
    assert st.day_prints == {}
    assert st.last_active_refresh_ms == NOW_MS


# --- OI refresh -------------------------------------------------------------------


def test_oi_refresh_handles_string_and_none(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    adapter = FakeAdapter(
        contracts_by_u={
            "AAPL": [
                _contract("AAPL260821C00150000", underlying="AAPL", oi="550"),
                _contract("AAPL260821P00140000", underlying="AAPL", oi=None),
            ],
        },
        snapshots_by_u={"AAPL": {}},
    )
    st = PollState()
    run_poll_once(
        adapter,
        symbols=["AAPL"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
        db_path=db,
    )
    assert st.oi["AAPL260821C00150000"] == 550
    assert "AAPL260821P00140000" not in st.oi


# --- underlying eligibility ---------------------------------------------------------


def test_eligible_underlyings_drops_futures_and_warns_over_cap(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Futures roots can't carry Alpaca options; over-cap must be loud, not silent."""
    import logging

    import signals.options_flow.scan as scan_mod
    from signals.options_flow.scan import eligible_underlyings

    monkeypatch.setattr(scan_mod, "OPTIONS_FLOW_MAX_UNDERLYINGS", 3)
    with caplog.at_level(logging.WARNING, logger="signals.options_flow.scan"):
        got = eligible_underlyings(["/ES", "/CL", "AAPL", "MSFT", "NVDA", "SPY", "TSLA"])

    # /ES and /CL used to burn 2 of the slots (and a 400ing REST call) each cycle.
    assert got == ["AAPL", "MSFT", "NVDA"]
    assert "NOT watching SPY,TSLA" in caplog.text


def test_active_set_pages_the_chain_instead_of_the_bare_vendor_page() -> None:
    """The bare call returns the vendor's 100-contract ALPHABETICAL page.

    An OCC sorts expiry -> C before P -> strike, so one bare page = one expiry of
    calls and never a put. Measured 2026-07-14: SPY returned 100 calls of 13,730
    live contracts (6,865 of them puts), which made put_net structurally zero.
    """
    adapter = _base_adapter()
    st = PollState()
    run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
    )
    assert adapter.chain_calls, "active-set refresh never fetched the chain"
    _u, limit, max_pages = adapter.chain_calls[0]
    assert max_pages > 1, "one page IS the vendor's alphabetical default — no puts"
    assert limit and limit > 100, "the vendor's 100-per-page default is the bug"
    assert limit == C.CHAIN_SNAPSHOT_PAGE_LIMIT
    assert max_pages == C.CHAIN_SNAPSHOT_MAX_PAGES


# --- active set -------------------------------------------------------------------


def test_active_set_ranks_and_caps_at_60(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "ACTIVE_SET_PER_UNDERLYING", 3)
    import signals.options_flow.poll as poll_mod

    monkeypatch.setattr(poll_mod, "ACTIVE_SET_PER_UNDERLYING", 3)

    snaps: dict[str, dict] = {}
    for i in range(5):
        occ = f"NVDA260821C{i:08d}"
        # higher i → newer ts and larger size
        snaps[occ] = _snapshot_trade(
            ts=f"{TODAY}T14:{i:02d}:00Z",
            size=100 + i,
        )
    adapter = FakeAdapter(
        contracts_by_u={"NVDA": []},
        snapshots_by_u={"NVDA": snaps},
    )
    st = PollState()
    run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
    )
    assert len(st.active) == 3
    assert "NVDA260821C00000004" in st.active
    assert "NVDA260821C00000003" in st.active
    assert "NVDA260821C00000002" in st.active
    assert "NVDA260821C00000000" not in st.active


def test_active_set_allocates_budget_round_robin_not_alphabetically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over budget, every underlying keeps its top picks — no ticker dies by spelling.

    _desired_trades() clips sorted(state.active) at STREAM_TRADES_MAX, so anything
    handed to it past the budget is dropped purely alphabetically (SPY/TSLA sort last).
    """
    import signals.options_flow.poll as poll_mod

    monkeypatch.setattr(poll_mod, "STREAM_TRADES_MAX", 4)
    monkeypatch.setattr(poll_mod, "ACTIVE_SET_PER_UNDERLYING", 60)

    snaps_by_u: dict[str, dict] = {}
    for u in ("AAAA", "ZZZZ"):
        snaps_by_u[u] = {
            f"{u}260821C{i:08d}": _snapshot_trade(ts=f"{TODAY}T14:{i:02d}:00Z", size=10 + i)
            for i in range(5)
        }
    adapter = FakeAdapter(
        contracts_by_u={"AAAA": [], "ZZZZ": []}, snapshots_by_u=snaps_by_u
    )
    st = PollState()
    run_poll_once(
        adapter,
        symbols=["AAAA", "ZZZZ"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
    )

    assert len(st.active) == 4
    first = [o for o in st.active if o.startswith("AAAA")]
    last = [o for o in st.active if o.startswith("ZZZZ")]
    assert len(first) == 2 and len(last) == 2, f"budget not shared: {sorted(st.active)}"
    # and each kept its OWN top-ranked contract (i=4 is newest+largest)
    assert {"AAAA260821C00000004", "ZZZZ260821C00000004"} <= set(st.active)


def test_active_set_seeds_from_oi_when_nothing_traded_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the bell the today-trade ranking is empty by construction; seed from OI.

    Without this the stream stayed unsubscribed through the open burst.
    """
    import signals.options_flow.poll as poll_mod

    monkeypatch.setattr(poll_mod, "ACTIVE_SET_PER_UNDERLYING", 2)

    yesterday = "2026-07-08"
    adapter = FakeAdapter(
        contracts_by_u={
            "NVDA": [
                _contract("NVDA260821C00190000", oi=100),
                _contract("NVDA260821C00200000", oi=900),
                _contract("NVDA260821C00210000", oi=500),
            ],
        },
        snapshots_by_u={
            "NVDA": {
                "NVDA260821C00190000": _snapshot_trade(ts=f"{yesterday}T19:59:00Z"),
                "NVDA260821C00200000": _snapshot_trade(ts=f"{yesterday}T19:59:00Z"),
                "NVDA260821C00210000": _snapshot_trade(ts=f"{yesterday}T19:59:00Z"),
            },
        },
    )
    st = PollState()
    run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
    )
    assert set(st.active) == {"NVDA260821C00200000", "NVDA260821C00210000"}


def test_oi_seed_stays_inside_stream_trade_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every underlying seeds at once, so the seed must fit the stream's sub budget.

    _desired_trades() clips alphabetically, so overflowing it would silently drop
    whole underlyings from the back of the alphabet.
    """
    import signals.options_flow.poll as poll_mod

    monkeypatch.setattr(poll_mod, "STREAM_TRADES_MAX", 4)
    monkeypatch.setattr(poll_mod, "ACTIVE_SET_PER_UNDERLYING", 60)

    yesterday = "2026-07-08"
    contracts_by_u: dict[str, list[dict]] = {}
    snapshots_by_u: dict[str, dict] = {}
    for u in ("AAPL", "NVDA"):
        contracts_by_u[u] = [
            _contract(f"{u}260821C{i:08d}", oi=100 + i, underlying=u) for i in range(5)
        ]
        snapshots_by_u[u] = {
            f"{u}260821C{i:08d}": _snapshot_trade(ts=f"{yesterday}T19:59:00Z")
            for i in range(5)
        }
    adapter = FakeAdapter(
        contracts_by_u=contracts_by_u, snapshots_by_u=snapshots_by_u
    )
    st = PollState()
    run_poll_once(
        adapter,
        symbols=["AAPL", "NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
    )
    # budget 4 over 2 underlyings → top 2 by OI each, not 5 each
    assert len(st.active) == 4
    assert set(st.active) == {
        "AAPL260821C00000004",
        "AAPL260821C00000003",
        "NVDA260821C00000004",
        "NVDA260821C00000003",
    }


def test_active_set_prefers_today_trades_over_oi_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once real prints exist the OI seed must not shadow them."""
    import signals.options_flow.poll as poll_mod

    monkeypatch.setattr(poll_mod, "ACTIVE_SET_PER_UNDERLYING", 1)

    adapter = FakeAdapter(
        contracts_by_u={
            "NVDA": [
                _contract("NVDA260821C00190000", oi=100),
                _contract("NVDA260821C00200000", oi=900),
            ],
        },
        snapshots_by_u={
            # only the low-OI contract has actually traded today
            "NVDA": {"NVDA260821C00190000": _snapshot_trade(ts=f"{TODAY}T14:00:00Z")},
        },
    )
    st = PollState()
    run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
    )
    assert set(st.active) == {"NVDA260821C00190000"}


# --- UV-1 piggyback ---------------------------------------------------------------


def test_uv1_fires_from_bars_and_oi_on_refresh(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    adapter = FakeAdapter(
        contracts_by_u={"NVDA": [_contract(OCC, oi=400)]},
        snapshots_by_u={"NVDA": {OCC: _snapshot_trade(ts=f"{TODAY}T14:00:00Z")}},
        bars_by_occ={OCC: [_bar(v=500, c=0.50)]},  # ratio 1.25, premium 25k
    )
    summary = run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        db_path=db,
    )
    assert summary["uv1"] == 1
    assert len(summary["events"]) == 1
    assert summary["events"][0]["event_type"] == "unusual_volume"
    events = of_db.recent_events(symbol="NVDA", db_path=db)
    uv1 = [e for e in events if e["event_type"] == "unusual_volume"]
    assert len(uv1) == 1
    assert uv1[0]["occ_symbol"] == OCC
    assert uv1[0]["id"] == f"of_{TODAY}_NVDA_{OCC}"
    assert uv1[0]["ts"] == _ts_ms_to_iso(NOW_MS)
    assert summary["events"][0]["ts"] == _ts_ms_to_iso(NOW_MS)


# --- cursor advance ---------------------------------------------------------------


def test_cursor_advance_filters_seen_trades(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    t1 = f"{TODAY}T14:30:00.000Z"
    t2 = f"{TODAY}T15:00:00.000Z"
    adapter = FakeAdapter(
        contracts_by_u={"NVDA": [_contract(OCC)]},
        snapshots_by_u={"NVDA": {OCC: _snapshot_trade(ts=t2, size=10)}},
        trades_by_occ={OCC: [_trade(ts=t1), _trade(ts=t2)]},
        quotes_by_occ={OCC: {"bid": 4.0, "ask": 4.6, "ts": t2}},
    )
    st = PollState()

    s1 = run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
        db_path=db,
    )
    assert s1["prints"] == 2
    assert st.cursors[OCC] == t2

    adapter.trades_by_occ = {OCC: [_trade(ts=t1), _trade(ts=t2)]}
    s2 = run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS + 30_000,
        state=st,
        db_path=db,
    )
    assert s2["prints"] == 0
    assert len(adapter.trades_calls) == 2
    assert adapter.trades_calls[-1][1] == t2


# --- block E2E --------------------------------------------------------------------


def test_block_e2e_trade_quote_direction(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    ts = f"{TODAY}T14:30:00.123Z"
    adapter = FakeAdapter(
        contracts_by_u={"NVDA": [_contract(OCC)]},
        snapshots_by_u={"NVDA": {OCC: _snapshot_trade(ts=ts, size=500)}},
        trades_by_occ={
            OCC: [_trade(ts=ts, price=4.5, size=400)],
        },
        quotes_by_occ={
            OCC: {"bid": 4.0, "ask": 4.5, "ts": ts},
        },
    )
    st = PollState()
    summary = run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
        db_path=db,
    )
    assert summary["prints"] == 1
    assert summary["blocks"] == 1
    assert len(summary["events"]) == 1
    assert summary["events"][0]["event_type"] == "block"

    events = of_db.recent_events(event_type="block", db_path=db)
    assert len(events) == 1
    ev = events[0]
    assert ev["direction"] == "buy_side"
    assert ev["direction_basis"] == "polled_quote"
    assert ev["occ_symbol"] == OCC
    assert ev["contracts"] == 400
    assert ev["premium_usd"] == 180_000.0


# --- directional pressure ---------------------------------------------------------


def test_pressure_e2e_stable_id(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    ts1 = f"{TODAY}T14:30:00Z"
    ts2 = f"{TODAY}T14:31:00Z"
    adapter = FakeAdapter(
        contracts_by_u={"NVDA": [_contract(OCC), _contract(OCC2, strike=200.0)]},
        snapshots_by_u={
            "NVDA": {
                OCC: _snapshot_trade(ts=ts1, size=10),
                OCC2: _snapshot_trade(ts=ts2, size=10),
            },
        },
        trades_by_occ={
            OCC: [_trade(ts=ts1, price=10.0, size=300)],  # 300k buy-side
            OCC2: [_trade(ts=ts2, price=10.0, size=50)],  # extra classified prem
        },
        quotes_by_occ={
            OCC: {"bid": 9.5, "ask": 10.0, "ts": ts1},
            OCC2: {"bid": 9.5, "ask": 10.0, "ts": ts2},
        },
    )
    st = PollState()
    summary = run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
        db_path=db,
    )
    assert summary["pressure"] == 1
    events = of_db.recent_events(event_type="directional_pressure", db_path=db)
    assert len(events) == 1
    assert events[0]["id"] == f"of_{TODAY}_NVDA_pressure"
    assert events[0]["direction"] == "buy_side"
    assert events[0]["direction_basis"] == "polled_quote"
    assert events[0]["ts"] == _ts_ms_to_iso(NOW_MS)
    assert summary["events"][-1]["ts"] == _ts_ms_to_iso(NOW_MS)


# --- spread exclusion -------------------------------------------------------------


def test_spread_tagged_prints_excluded_from_blocks(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    ts = f"{TODAY}T14:30:00Z"
    adapter = FakeAdapter(
        contracts_by_u={"NVDA": [_contract(OCC)]},
        snapshots_by_u={"NVDA": {OCC: _snapshot_trade(ts=ts, size=500)}},
        trades_by_occ={
            OCC: [_trade(ts=ts, price=4.5, size=400, conditions=["f"])],
        },
        quotes_by_occ={OCC: {"bid": 4.0, "ask": 4.5, "ts": ts}},
    )
    st = PollState()
    summary = run_poll_once(
        adapter,
        symbols=["NVDA"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
        db_path=db,
    )
    assert summary["prints"] == 1
    assert summary["blocks"] == 0
    assert of_db.recent_events(event_type="block", db_path=db) == []


# --- fail-soft --------------------------------------------------------------------


def test_adapter_raise_on_one_underlying_does_not_kill_cycle(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    ts = f"{TODAY}T14:30:00Z"
    adapter = FakeAdapter(
        contracts_by_u={
            "NVDA": [_contract(OCC)],
            "BOOM": [_contract("BOOM260821C00010000", underlying="BOOM")],
        },
        snapshots_by_u={
            "NVDA": {OCC: _snapshot_trade(ts=ts, size=10)}},
        trades_by_occ={OCC: [_trade(ts=ts, price=4.5, size=400)]},
        quotes_by_occ={OCC: {"bid": 4.0, "ask": 4.5, "ts": ts}},
        raise_snapshot_on={"BOOM"},
    )
    st = PollState()
    summary = run_poll_once(
        adapter,
        symbols=["NVDA", "BOOM"],
        today_et=TODAY,
        now_ms=NOW_MS,
        state=st,
        db_path=db,
    )
    assert summary["prints"] == 1
    assert summary["blocks"] == 1


# --- print cap --------------------------------------------------------------------


def test_per_underlying_print_cap_enforced() -> None:
    st = PollState()
    tiny = [{"size": i} for i in range(5)]
    import signals.options_flow.poll as poll_mod

    original_cap = poll_mod._DAY_PRINTS_CAP
    poll_mod._DAY_PRINTS_CAP = 3
    try:
        _append_day_prints(st, "NVDA", tiny)
        assert len(st.day_prints["NVDA"]) == 3
        assert st.day_prints["NVDA"][-1]["size"] == 4
    finally:
        poll_mod._DAY_PRINTS_CAP = original_cap


# --- Gauge lane (audit 2026-07-16 F1) -------------------------------------------------


def test_gauge_lane_subscribes_index_zero_dte() -> None:
    """SPY 0-1 DTE stays OUT of the regular set (detectors discard those prints)
    but lands in the active set via the gauge lane, flagged gauge_lane."""
    snaps = {
        # 0DTE (expiry == TODAY) — the regular path skips these for SPY
        "SPY260709C00629000": _snapshot_trade(ts=f"{TODAY}T14:01:00Z", size=500),
        "SPY260709P00627000": _snapshot_trade(ts=f"{TODAY}T14:02:00Z", size=400),
        # far-dated — regular pick
        "SPY260821C00640000": _snapshot_trade(ts=f"{TODAY}T14:03:00Z", size=100),
    }
    adapter = FakeAdapter(contracts_by_u={"SPY": []}, snapshots_by_u={"SPY": snaps})
    st = PollState()
    run_poll_once(adapter, symbols=["SPY"], today_et=TODAY, now_ms=NOW_MS, state=st)

    assert "SPY260821C00640000" in st.active
    assert st.active["SPY260821C00640000"].get("gauge_lane") is None
    assert "SPY260709C00629000" in st.active
    assert st.active["SPY260709C00629000"]["gauge_lane"] is True
    assert "SPY260709P00627000" in st.active
    assert st.active["SPY260709P00627000"]["gauge_lane"] is True


def test_gauge_universe_fetched_beyond_watchlist() -> None:
    """Gauge names are guaranteed coverage — fetched even when no watchlist has
    them, outside the watchlist cap (audit F1: IWM/DIA/SPX etc. accrued zero rows
    forever because the underlying set was watchlist-only)."""
    snaps = {"QQQ260821C00500000": _snapshot_trade(ts=f"{TODAY}T14:01:00Z", size=50)}
    adapter = FakeAdapter(contracts_by_u={"NVDA": []}, snapshots_by_u={"QQQ": snaps})
    st = PollState()
    run_poll_once(adapter, symbols=["NVDA"], today_et=TODAY, now_ms=NOW_MS, state=st)

    fetched = {u for u, _, _ in adapter.chain_calls}
    assert {"NVDA", "SPY", "QQQ", "IWM", "DIA", "SPX", "AMZN"} <= fetched
    assert "QQQ260821C00500000" in st.active  # picked without any watchlist


def test_gauge_lane_survives_budget_squeeze(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gauge picks allocate BEFORE the regular budget — an over-budget regular set
    can never evict them (the PR #73 alphabetical-clip lesson, applied to the lane)."""
    import signals.options_flow.poll as poll_mod

    monkeypatch.setattr(poll_mod, "STREAM_TRADES_MAX", 3)

    snaps_spy = {
        "SPY260709C00629000": _snapshot_trade(ts=f"{TODAY}T14:01:00Z", size=500),
    }
    snaps_nvda = {
        f"NVDA260821C{i:08d}": _snapshot_trade(ts=f"{TODAY}T14:{i:02d}:00Z", size=10 + i)
        for i in range(5)
    }
    adapter = FakeAdapter(
        contracts_by_u={"NVDA": [], "SPY": []},
        snapshots_by_u={"SPY": snaps_spy, "NVDA": snaps_nvda},
    )
    st = PollState()
    run_poll_once(
        adapter, symbols=["NVDA", "SPY"], today_et=TODAY, now_ms=NOW_MS, state=st
    )

    assert len(st.active) == 3
    assert st.active["SPY260709C00629000"]["gauge_lane"] is True  # survived
    assert sum(1 for o in st.active if o.startswith("NVDA")) == 2  # clipped remainder


def test_oi_seed_covers_gauge_names_beyond_watchlist() -> None:
    """The OI seed is the bell's subscription — gauge names must be seeded even
    when no watchlist carries them, or the open window's first minutes are blind
    for exactly the names the gauge exists to measure (audit F1)."""
    from signals.options_flow.poll import _refresh_oi

    contracts = [
        {"symbol": "IWM260709C00220000", "open_interest": 5000},
        {"symbol": "IWM260709P00218000", "open_interest": 4000},
    ]
    adapter = FakeAdapter(contracts_by_u={"IWM": contracts})
    st = PollState()
    _refresh_oi(adapter, ["NVDA"], TODAY, st)

    assert st.oi_seed.get("IWM") == ["IWM260709C00220000", "IWM260709P00218000"]
    assert st.oi["IWM260709C00220000"] == 5000


def test_spx_zero_dte_routes_to_gauge_lane_not_detectors() -> None:
    """Review H1: SPX/SPXW get index treatment — SPX 0DTE goes to the gauge lane
    (not the regular set, where its prints would reach the burst/cluster/sweep
    detectors as index-0DTE noise the F-3 exclusion exists to prevent)."""
    snaps = {
        "SPXW260709C05500000": _snapshot_trade(ts=f"{TODAY}T14:01:00Z", size=200),
        "SPXW260821C05600000": _snapshot_trade(ts=f"{TODAY}T14:02:00Z", size=50),
    }
    adapter = FakeAdapter(contracts_by_u={"SPX": []}, snapshots_by_u={"SPX": snaps})
    st = PollState()
    run_poll_once(adapter, symbols=[], today_et=TODAY, now_ms=NOW_MS, state=st)

    assert st.active["SPXW260709C05500000"]["gauge_lane"] is True  # 0DTE → gauge lane
    assert st.active["SPXW260821C05600000"].get("gauge_lane") is None  # far → regular


def test_spxw_dailies_recovered_by_per_expiry_fetch() -> None:
    """SPXW pagination trap (live-caught 2026-07-16): SPX monthlies sort before
    SPXW dailies, so the paged whole-chain fetch never reaches the 0DTE dailies.
    Gauge index names re-fetch the next two expiries explicitly."""
    monthly = {"SPX260821C05600000": _snapshot_trade(ts=f"{TODAY}T14:01:00Z", size=30)}
    dailies = {"SPXW260709C05500000": _snapshot_trade(ts=f"{TODAY}T14:02:00Z", size=400)}
    adapter = FakeAdapter(
        contracts_by_u={"SPX": []},
        snapshots_by_u={"SPX": monthly},  # what the paged whole-chain fetch sees
        snapshots_by_u_exp={("SPX", TODAY): dailies},  # only per-expiry reaches these
    )
    st = PollState()
    run_poll_once(adapter, symbols=[], today_et=TODAY, now_ms=NOW_MS, state=st)

    assert "SPXW260709C05500000" in st.active  # recovered
    assert st.active["SPXW260709C05500000"]["gauge_lane"] is True  # 0DTE → gauge lane
    exp_calls = [c for c in adapter.chain_calls if c[0] == "SPX"]
    assert len(exp_calls) >= 3  # whole-chain + two per-expiry fetches
