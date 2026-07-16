"""Hermetic tests for options-flow convergence watch + push budget."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from signals.options_flow import constants as C
from signals.options_flow.convergence import (
    build_convergence,
    is_solo_push,
    nearest_level,
    process_events,
    rvol_from_profile,
)
from signals.volume_profile import N_BUCKETS

TODAY = "2026-07-09"


def _linear_cumfrac() -> list[float]:
    return [(i + 1) / N_BUCKETS for i in range(N_BUCKETS)]


def _sample_event(**overrides: Any) -> dict:
    base = {
        "id": "of_2026-07-09_NVDA_blk",
        "underlying": "NVDA",
        "event_type": "block",
        "right": "call",
        "direction": "buy_side",
        "premium_usd": 1_400_000,
        "reason": "NVDA Aug $190C: block 4,100 contracts, $1.4M premium, buy_side",
        "caveat": "direction inferred from quote rule",
    }
    base.update(overrides)
    return base


# --- pure: rvol_from_profile ------------------------------------------------------


def test_rvol_parity_expected_volume() -> None:
    cumfrac = _linear_cumfrac()
    # bucket 10 → minutes_since_open 50
    median = 100_000.0
    bucket = 10
    expected = median * cumfrac[bucket]
    total = expected * 2.3
    got = rvol_from_profile(
        median_daily_total=median,
        cumfrac=cumfrac,
        total_volume=total,
        minutes_since_open=50,
    )
    assert got == pytest.approx(2.3)


def test_rvol_expected_non_positive_returns_none() -> None:
    cumfrac = [0.0] * N_BUCKETS
    assert (
        rvol_from_profile(
            median_daily_total=100_000,
            cumfrac=cumfrac,
            total_volume=50_000,
            minutes_since_open=50,
        )
        is None
    )


def test_rvol_bucket_clamps_at_77() -> None:
    cumfrac = _linear_cumfrac()
    median = 100_000.0
    bucket = 77
    total = median * cumfrac[bucket] * 1.5
    got = rvol_from_profile(
        median_daily_total=median,
        cumfrac=cumfrac,
        total_volume=total,
        minutes_since_open=10_000,
    )
    assert got == pytest.approx(1.5)


# --- pure: nearest_level ----------------------------------------------------------


def test_nearest_level_inside_atr_threshold() -> None:
    levels = [{"name": "VAH", "price": 188.0, "kind": "resistance"}]
    hit = nearest_level(
        price=188.3,
        levels=levels,
        alert_prices=[],
        atr_value=1.0,
    )
    assert hit == {"name": "VAH", "price": 188.0, "dist_atr": 0.3}


def test_nearest_level_outside_atr_returns_none() -> None:
    levels = [{"name": "VAH", "price": 188.0, "kind": "resistance"}]
    assert (
        nearest_level(
            price=200.0,
            levels=levels,
            alert_prices=[],
            atr_value=1.0,
        )
        is None
    )


def test_nearest_level_no_atr_returns_none() -> None:
    levels = [{"name": "VAH", "price": 188.0, "kind": "resistance"}]
    assert (
        nearest_level(
            price=188.0,
            levels=levels,
            alert_prices=[],
            atr_value=None,
        )
        is None
    )


def test_nearest_level_alert_beats_farther_node() -> None:
    levels = [{"name": "VAH", "price": 180.0, "kind": "resistance"}]
    hit = nearest_level(
        price=188.1,
        levels=levels,
        alert_prices=[188.0],
        atr_value=2.0,
    )
    assert hit == {"name": "alert", "price": 188.0, "dist_atr": 0.05}


# --- pure: build_convergence ------------------------------------------------------


def test_build_convergence_flow_plus_rvol() -> None:
    ev = _sample_event()
    built = build_convergence(
        event=ev,
        rvol=2.3,
        level=None,
        event_date=TODAY,
    )
    assert built is not None
    assert built["underlying"] == "NVDA"
    assert built["title"] == "NVDA: signal confluence"
    assert "RVOL 2.3x" in built["body"]
    assert "flow" in built["components"]
    assert "rvol" in built["components"]
    assert "level" not in built["components"]


def test_build_convergence_flow_plus_level() -> None:
    ev = _sample_event()
    built = build_convergence(
        event=ev,
        rvol=None,
        level={"name": "VAH", "price": 188.4},
        event_date=TODAY,
    )
    assert built is not None
    assert "at VAH $188.40" in built["body"]
    assert set(built["components"]) == {"flow", "level"}


def test_build_convergence_all_three_components() -> None:
    ev = _sample_event()
    built = build_convergence(
        event=ev,
        rvol=2.0,
        level={"name": "VAH", "price": 188.4},
        event_date=TODAY,
    )
    assert built is not None
    assert set(built["components"]) == {"flow", "rvol", "level"}


def test_build_convergence_flow_alone_does_not_fire() -> None:
    ev = _sample_event()
    assert (
        build_convergence(event=ev, rvol=None, level=None, event_date=TODAY) is None
    )


def _sample_tape_event(**overrides) -> dict:
    base = {
        "id": f"eq_{TODAY}_NVDA_dp_123",
        "ts": f"{TODAY}T15:30:00Z",
        "symbol": "NVDA",
        "event_type": "dark_pool",
        "notional_usd": 8_300_000.0,
        "direction": "undetermined",
        "reason": "$8.3M print",
        "caveat": "tape fact",
    }
    base.update(overrides)
    return base


def test_build_convergence_tape_plus_level() -> None:
    built = build_convergence(
        tape_event=_sample_tape_event(),
        rvol=None,
        level={"name": "VAH", "price": 188.4},
        event_date=TODAY,
    )
    assert built is not None
    assert built["underlying"] == "NVDA"
    assert set(built["components"]) == {"tape", "level"}
    assert "dark-pool print" in built["body"]
    assert built["event_id"] == f"eq_{TODAY}_NVDA_dp_123"


def test_build_convergence_flow_plus_tape() -> None:
    built = build_convergence(
        event=_sample_event(),
        tape_event=_sample_tape_event(event_type="equity_block"),
        rvol=None,
        level=None,
        event_date=TODAY,
    )
    assert built is not None
    assert set(built["components"]) == {"flow", "tape"}
    assert "block print" in built["body"]
    # flow lead owns the ref id
    assert built["event_id"] == _sample_event()["id"]


def test_build_convergence_tape_alone_does_not_fire() -> None:
    assert (
        build_convergence(
            tape_event=_sample_tape_event(),
            rvol=None,
            level=None,
            event_date=TODAY,
        )
        is None
    )


# --- pure: is_solo_push -----------------------------------------------------------


def test_is_solo_push_threshold_boundary() -> None:
    solo_min = C.SOLO_PUSH_PREMIUM_USD
    ev = _sample_event(event_type="unusual_volume", premium_usd=solo_min)
    assert is_solo_push(ev) is True
    assert is_solo_push({**ev, "premium_usd": solo_min - 1}) is False
    assert is_solo_push({**ev, "event_type": "directional_pressure"}) is False


# --- process_events: budget + dedup + I/O -----------------------------------------


@dataclass
class _NotifyCall:
    kind: str
    title: str
    body: str
    push: bool
    data: dict | None
    ref_id: str | None


class FakeAdapter:
    def __init__(
        self,
        *,
        snapshot: dict[str, float] | None = None,
        bars: list[Any] | None = None,
        raise_snapshot: bool = False,
        raise_bars: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.bars = bars or []
        self.raise_snapshot = raise_snapshot
        self.raise_bars = raise_bars

    def get_stock_snapshot_fields(self, symbol: str) -> dict[str, float] | None:
        del symbol
        if self.raise_snapshot:
            raise RuntimeError("snapshot boom")
        return dict(self.snapshot) if self.snapshot else None

    def get_stock_bars(self, symbol: str, *, timeframe: str = "1Day", days: int = 30):
        del symbol, timeframe, days
        if self.raise_bars:
            raise RuntimeError("bars boom")
        return list(self.bars)


class _StockBar:
    def __init__(self, high: float, low: float, close: float) -> None:
        self.high = high
        self.low = low
        self.close = close


@pytest.fixture
def dedup_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    store: dict[str, bool] = {}

    def was_sent(key: str, *, db_path: Path | None = None) -> bool:
        del db_path
        return store.get(key, False)

    def mark_sent(key: str, *, db_path: Path | None = None) -> None:
        del db_path
        store[key] = True

    monkeypatch.setattr("db.earnings_alerts.was_sent", was_sent)
    monkeypatch.setattr("db.earnings_alerts.mark_sent", mark_sent)
    monkeypatch.setattr(
        "signals.options_flow.convergence.was_sent",
        was_sent,
        raising=False,
    )
    monkeypatch.setattr(
        "signals.options_flow.convergence.mark_sent",
        mark_sent,
        raising=False,
    )
    return store


@pytest.fixture
def notify_recorder(monkeypatch: pytest.MonkeyPatch) -> list[_NotifyCall]:
    calls: list[_NotifyCall] = []

    async def _fake_notify(**kwargs: Any) -> dict:
        calls.append(
            _NotifyCall(
                kind=str(kwargs.get("kind") or ""),
                title=str(kwargs.get("title") or ""),
                body=str(kwargs.get("body") or ""),
                push=bool(kwargs.get("push", True)),
                data=kwargs.get("data"),
                ref_id=kwargs.get("ref_id"),
            )
        )
        return {"id": f"n{len(calls)}"}

    monkeypatch.setattr("signals.options_flow.convergence.notify", _fake_notify)
    return calls


def _process_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile,
    session_levels: dict | None,
    adapter: FakeAdapter,
    alert_prices: list[float] | None = None,
    max_push_per_day: int = 5,
) -> None:
    monkeypatch.setattr(
        "signals.options_flow.convergence.load_profile",
        lambda symbol, **kw: profile,
    )

    async def _levels(symbol: str = "", **kw: Any) -> dict | None:
        del symbol, kw
        return session_levels

    monkeypatch.setattr(
        "signals.options_flow.convergence.compute_session_levels",
        _levels,
    )

    def _alerts(*, symbol: str | None = None, active_only: bool = True) -> list[dict]:
        del symbol, active_only
        return [{"price": p} for p in (alert_prices or [])]

    monkeypatch.setattr(
        "signals.options_flow.convergence.list_price_alerts",
        _alerts,
    )

    # Hermetic push budget — never read the real data/settings.json in tests.
    monkeypatch.setattr(
        "config.settings.alerts_config",
        lambda: {"max_push_per_day": max_push_per_day},
    )

    # Hermetic day-memory: the cross-source backfill must not touch the real DB.
    monkeypatch.setattr(
        "signals.options_flow.convergence._today_flow_lead",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "signals.options_flow.convergence._today_tape_lead",
        lambda *a, **k: None,
    )


async def _run_process(
    adapter: FakeAdapter,
    events: list[dict],
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile,
    session_levels: dict | None = None,
    minutes_since_open: int = 50,
    max_push_per_day: int = 5,
) -> dict[str, int]:
    _process_env(
        monkeypatch,
        profile=profile,
        session_levels=session_levels,
        adapter=adapter,
        max_push_per_day=max_push_per_day,
    )
    return await process_events(
        adapter,
        events,
        today_et=TODAY,
        minutes_since_open=minutes_since_open,
    )


@pytest.mark.asyncio
async def test_process_convergence_persists_and_marks_dedup(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    cumfrac = _linear_cumfrac()
    profile = type(
        "P",
        (),
        {"median_daily_total": 100_000.0, "cumfrac": cumfrac},
    )()
    adapter = FakeAdapter(
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 2.0},
        bars=[_StockBar(2, 1, 100)] * 20,
    )
    ev = _sample_event()
    summary = await _run_process(
        adapter,
        [ev],
        monkeypatch,
        profile=profile,
        session_levels={"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]},
    )
    assert summary["convergence"] == 1
    assert len(notify_recorder) == 1
    call = notify_recorder[0]
    assert call.kind == "convergence"
    assert call.push is True
    assert call.data is not None
    assert call.data["reason"] == ev["reason"]
    assert call.data["caveat"] == ev["caveat"]
    assert "win_prob" not in call.data
    assert "ev" not in call.data
    assert "confidence" not in call.data
    assert dedup_store[f"of_conv:NVDA:{TODAY}"] is True


@pytest.mark.asyncio
async def test_process_per_underlying_day_dedup_second_call_noops(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    cumfrac = _linear_cumfrac()
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": cumfrac})()
    adapter = FakeAdapter(
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 2.0},
        bars=[_StockBar(2, 1, 100)] * 20,
    )
    ev = _sample_event(premium_usd=500_000)
    await _run_process(
        adapter,
        [ev],
        monkeypatch,
        profile=profile,
        session_levels={"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]},
    )
    await _run_process(
        adapter,
        [ev],
        monkeypatch,
        profile=profile,
        session_levels={"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]},
    )
    assert len(notify_recorder) == 1


@pytest.mark.asyncio
async def test_process_budget_fifth_push_true_sixth_push_false(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    cumfrac = _linear_cumfrac()
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": cumfrac})()
    adapter = FakeAdapter(
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 2.0},
        bars=[_StockBar(2, 1, 100)] * 20,
    )
    session = {"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]}

    for i in range(6):
        ev = _sample_event(
            id=f"of_{TODAY}_SYM{i}_blk",
            underlying=f"SYM{i}",
        )
        await _run_process(
            adapter,
            [ev],
            monkeypatch,
            profile=profile,
            session_levels=session,
        )

    assert len(notify_recorder) == 6
    assert [c.push for c in notify_recorder].count(True) == 5
    assert notify_recorder[-1].push is False
    assert notify_recorder[-1].ref_id is not None


@pytest.mark.asyncio
async def test_process_budget_honors_alerts_setting(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    cumfrac = _linear_cumfrac()
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": cumfrac})()
    adapter = FakeAdapter(
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 2.0},
        bars=[_StockBar(2, 1, 100)] * 20,
    )
    session = {"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]}

    for i in range(3):
        ev = _sample_event(
            id=f"of_{TODAY}_SYM{i}_blk",
            underlying=f"SYM{i}",
        )
        await _run_process(
            adapter,
            [ev],
            monkeypatch,
            profile=profile,
            session_levels=session,
            max_push_per_day=2,
        )

    assert len(notify_recorder) == 3
    assert [c.push for c in notify_recorder].count(True) == 2
    assert notify_recorder[-1].push is False


@pytest.mark.asyncio
async def test_process_budget_store_exception_still_persists_push_false(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    cumfrac = _linear_cumfrac()
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": cumfrac})()
    adapter = FakeAdapter(
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 2.0},
        bars=[_StockBar(2, 1, 100)] * 20,
    )

    def flaky_was_sent(key: str, *, db_path: Path | None = None) -> bool:
        del db_path
        if str(key).startswith("of_push:"):
            raise RuntimeError("budget store down")
        return dedup_store.get(key, False)

    monkeypatch.setattr("db.earnings_alerts.was_sent", flaky_was_sent)

    ev = _sample_event()
    summary = await _run_process(
        adapter,
        [ev],
        monkeypatch,
        profile=profile,
        session_levels={"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]},
    )
    assert summary["convergence"] == 1
    assert summary["pushes"] == 0
    assert len(notify_recorder) == 1
    assert notify_recorder[0].push is False


@pytest.mark.asyncio
async def test_process_solo_dedup_per_underlying_type_day(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    profile = None
    adapter = FakeAdapter()
    ev1 = _sample_event(
        event_type="block",
        premium_usd=C.SOLO_PUSH_PREMIUM_USD,
        id="of_solo_once",
    )
    # Distinct block print (different ts-based id) on the SAME underlying/day must
    # dedup against the first — one busy name can't drain the daily push budget.
    ev2 = _sample_event(
        event_type="block",
        premium_usd=C.SOLO_PUSH_PREMIUM_USD,
        id="of_solo_twice",
    )
    await _run_process(adapter, [ev1], monkeypatch, profile=profile)
    await _run_process(adapter, [ev2], monkeypatch, profile=profile)
    solo_calls = [c for c in notify_recorder if c.kind == "options_flow_solo"]
    assert len(solo_calls) == 1
    assert dedup_store[f"of_solo:NVDA:block:{TODAY}"] is True


@pytest.mark.asyncio
async def test_process_solo_skipped_when_convergence_covers_underlying(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    cumfrac = _linear_cumfrac()
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": cumfrac})()
    adapter = FakeAdapter(
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 2.0},
        bars=[_StockBar(2, 1, 100)] * 20,
    )
    ev = _sample_event(premium_usd=C.SOLO_PUSH_PREMIUM_USD)
    await _run_process(
        adapter,
        [ev],
        monkeypatch,
        profile=profile,
        session_levels={"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]},
    )
    kinds = [c.kind for c in notify_recorder]
    assert kinds == ["convergence"]
    assert "options_flow_solo" not in kinds


@pytest.mark.asyncio
async def test_process_adapter_failure_no_crash_missing_components(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": _linear_cumfrac()})()
    adapter = FakeAdapter(raise_snapshot=True, raise_bars=True)
    ev = _sample_event()
    summary = await _run_process(
        adapter,
        [ev],
        monkeypatch,
        profile=profile,
        session_levels=None,
    )
    assert summary == {"convergence": 0, "solo": 1, "pushes": 1}
    assert notify_recorder[0].kind == "options_flow_solo"


@pytest.mark.asyncio
async def test_process_mark_sent_only_after_successful_notify(
    monkeypatch: pytest.MonkeyPatch,
    dedup_store: dict[str, bool],
) -> None:
    cumfrac = _linear_cumfrac()
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": cumfrac})()
    adapter = FakeAdapter(
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 2.0},
        bars=[_StockBar(2, 1, 100)] * 20,
    )

    async def _empty_notify(**kwargs: Any) -> dict:
        del kwargs
        return {}

    monkeypatch.setattr("signals.options_flow.convergence.notify", _empty_notify)

    ev = _sample_event()
    summary = await _run_process(
        adapter,
        [ev],
        monkeypatch,
        profile=profile,
        session_levels={"levels": [{"name": "VAH", "price": 188.0, "kind": "r"}]},
    )
    assert summary["convergence"] == 0
    assert f"of_conv:NVDA:{TODAY}" not in dedup_store


@pytest.mark.asyncio
async def test_process_tape_event_converges_with_days_stored_flow(
    monkeypatch: pytest.MonkeyPatch,
    notify_recorder: list[_NotifyCall],
    dedup_store: dict[str, bool],
) -> None:
    """Morning sweep (already in the DB) + afternoon dark-pool print → confluence."""
    cumfrac = _linear_cumfrac()
    profile = type("P", (), {"median_daily_total": 100_000.0, "cumfrac": cumfrac})()
    adapter = FakeAdapter(
        # day_volume at 0.5× expected → RVOL below threshold, no rvol component
        snapshot={"last_price": 188.0, "day_volume": cumfrac[10] * 100_000 * 0.5},
        bars=[_StockBar(2, 1, 100)] * 20,
    )
    _process_env(monkeypatch, profile=profile, session_levels=None, adapter=adapter)
    # The day's stored flow lead (would come from options_flow_events)
    monkeypatch.setattr(
        "signals.options_flow.convergence._today_flow_lead",
        lambda *a, **k: _sample_event(),
    )

    tape = _sample_tape_event()
    summary = await process_events(
        adapter, [tape], today_et=TODAY, minutes_since_open=50
    )

    assert summary["convergence"] == 1
    call = notify_recorder[0]
    assert call.kind == "convergence"
    assert call.data is not None
    assert set(call.data["components"]) == {"flow", "tape"}
    assert "dark-pool print" in call.body
    assert f"of_conv:NVDA:{TODAY}" in dedup_store
