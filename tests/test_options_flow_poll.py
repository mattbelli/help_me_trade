"""Hermetic tests for options-flow intraday poll cycle (fake adapter, tmp db)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from db import options_flow as of_db
from signals.options_flow import constants as C
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
        trades_by_occ: dict[str, list[dict]] | None = None,
        quotes_by_occ: dict[str, dict] | None = None,
        bars_by_occ: dict[str, list[dict]] | None = None,
        conditions: dict[str, str] | None = None,
        raise_snapshot_on: set[str] | None = None,
    ) -> None:
        self.contracts_by_u = contracts_by_u or {}
        self.snapshots_by_u = snapshots_by_u or {}
        self.trades_by_occ = trades_by_occ or {}
        self.quotes_by_occ = quotes_by_occ or {}
        self.bars_by_occ = bars_by_occ or {}
        self.conditions = conditions or {"I": "electronic"}
        self.raise_snapshot_on = {s.upper() for s in (raise_snapshot_on or set())}
        self.trades_calls: list[tuple[list[str], str]] = []

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

    def get_option_chain_snapshot(self, underlying: str, feed: str = "opra") -> dict:
        del feed
        u = underlying.strip().upper()
        if u in self.raise_snapshot_on:
            raise RuntimeError(f"boom:{u}")
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
