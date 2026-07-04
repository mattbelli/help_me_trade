"""Regime-local sleeves (generals_dip + vol_event) — signals/regime_sleeves.py.

Covers the spec's non-negotiables: point-in-time universe, signal predicates,
cluster cap (incl. vix_spike counting) + suppression journaling, mechanical
kill lines (trip → auto-disable → journal → notify), VIX3M parse + staleness
dormancy, ships-disabled invariant, and Tier-B card labeling (win_prob=None).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import signals.regime_sleeves as rs
import trading.proposals as proposals_mod
from db.base import connect
from db.decision_journal import (
    by_strategy,
    record_decision,
    resolve_outcome,
    set_entry_fill,
)
from db.managed_positions import list_open_positions, record_open_position
from db.paper_orders import create_pending_proposal, transition_proposal_status
from db.regime import latest_observation, upsert_regime
from db.session_node import get_payload, mark
from db.strategies import get_strategy, seed_default_strategies, update_strategy
from signals.regime import parse_fred_csv, vix3m_is_fresh
from signals.regime_sleeves import (
    GENERALS_ID,
    VOL_EVENT_ID,
    _generals_kill_check,
    _vol_event_exits,
    _vol_event_kill_checks,
    compute_top30_universe,
    is_drawdown_stab_entry,
    is_generals_dip_entry,
    is_term_flip_entry,
    run_regime_sleeves_scan,
    sleeves_status,
)
from signals.strategy_kinds import strategy_kind
from signals.strategy_stats import strategy_conviction
from trading.exit_engine import _strategy_kind as exit_engine_kind

_ET = ZoneInfo("America/New_York")
# Friday 2026-07-03, 17:30 ET — inside the post-close window.
_NOW = datetime(2026, 7, 3, 17, 30, tzinfo=_ET)
_EVAL_DATE = "2026-07-03"
# Exit-throttle markers are stamped with the REAL clock — those tests eval "today" ET.
_TODAY_ET = datetime.now(_ET).date().isoformat()


def _bars(closes: list[float], *, end: str = _EVAL_DATE, lows: list[float] | None = None):
    """DatedBars ending at `end`, one per calendar day (dates only need to be ordered)."""
    end_d = datetime.fromisoformat(end).date()
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        d = (end_d - timedelta(days=n - 1 - i)).isoformat()
        low = lows[i] if lows is not None else c - 0.5
        out.append({"d": d, "o": c, "h": c + 0.5, "l": low, "c": c, "v": 1_000_000})
    return out


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "sleeves.db"


@pytest.fixture
def scan_env(monkeypatch: pytest.MonkeyPatch):
    """Gate/account/proposal stubs for scan-level tests."""
    proposal_calls: list[dict] = []
    notify_calls: list[dict] = []

    async def fake_create_phone_proposal(args, **kwargs):
        proposal_calls.append({"args": args, **kwargs})
        return {"id": f"prop_{len(proposal_calls)}", "expires_at": "x", "created_at": "x"}

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {}

    monkeypatch.setattr(
        rs, "trading_config",
        lambda: {"agent_proposals_enabled": True, "max_pending_agent_proposals": 3},
    )
    monkeypatch.setattr(rs, "is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        rs, "_resolve_account",
        lambda strategy, *, db_path: {"id": "acct_1", "broker": "alpaca", "execution_capable": 1},
    )
    monkeypatch.setattr(proposals_mod, "create_phone_proposal", fake_create_phone_proposal)
    monkeypatch.setattr(rs, "notify", fake_notify)
    return {"proposals": proposal_calls, "notifies": notify_calls}


def _seed_regime(db: Path, rows: list[tuple[str, str, float]]) -> None:
    upsert_regime(rows, db_path=db)


def _patch_bars(monkeypatch, bars_by_symbol: dict):
    async def fake_fetch(symbols, *, days):
        return {s: bars_by_symbol[s] for s in symbols if s in bars_by_symbol}

    monkeypatch.setattr(rs, "_fetch_daily_bars", fake_fetch)


# --- VIX3M: CSV parse + staleness --------------------------------------------------------


def test_parse_fred_csv_skips_missing_and_orders_newest_first() -> None:
    text = (
        "observation_date,VXVCLS\n"
        "2026-06-29,24.10\n"
        "2026-06-30,.\n"
        "2026-07-01,23.55\n"
        "garbage line\n"
        "2026-07-02,22.80\n"
    )
    obs = parse_fred_csv(text)
    assert obs == [("2026-07-02", 22.8), ("2026-07-01", 23.55), ("2026-06-29", 24.1)]
    assert parse_fred_csv(text, limit=1) == [("2026-07-02", 22.8)]


def test_refresh_regime_macro_upserts_vix3m_without_fred_key(
    db: Path, monkeypatch
) -> None:
    import signals.regime as regime_mod

    monkeypatch.setattr(regime_mod, "fred_is_configured", lambda: False)
    monkeypatch.setattr(
        regime_mod, "fetch_fred_csv", lambda series_id, **kw: [("2026-07-01", 25.5)]
    )
    written = asyncio.run(regime_mod.refresh_regime_macro(db_path=db))
    assert written == 1
    assert latest_observation("vix3m", db_path=db) == ("2026-07-01", 25.5)


def test_vix3m_freshness_window(db: Path) -> None:
    today = datetime(2026, 7, 3).date()  # Friday
    _seed_regime(db, [("vix3m", "2026-07-01", 24.0)])
    assert vix3m_is_fresh(db_path=db, now=today) is True  # 2 trading days
    _seed_regime(db, [("vix3m", "2026-06-19", 24.0)])
    with connect(db) as conn:
        conn.execute("DELETE FROM regime_macro WHERE date > '2026-06-19'")
        conn.commit()
    assert vix3m_is_fresh(db_path=db, now=today) is False  # ~10 trading days
    with connect(db) as conn:
        conn.execute("DELETE FROM regime_macro")
        conn.commit()
    assert vix3m_is_fresh(db_path=db, now=today) is False  # missing series


# --- universe: point-in-time top-30 -------------------------------------------------------


def test_top30_universe_is_point_in_time_and_excludes_etfs() -> None:
    as_of = "2026-06-30"
    steady = _bars([100.0] * 70, end="2026-06-30")
    # LOWV: tiny volume before as_of, HUGE volume after — must NOT be promoted.
    low_then_huge = [dict(b, v=1_000) for b in _bars([100.0] * 70, end="2026-06-30")]
    low_then_huge += [dict(b, v=1_000_000_000) for b in _bars([100.0] * 5, end="2026-07-05")]
    short_history = _bars([100.0] * 10, end="2026-06-30")
    spy = [dict(b, v=10_000_000_000) for b in _bars([100.0] * 70, end="2026-06-30")]

    bars = {"AAA": steady, "LOWV": low_then_huge, "NEWIPO": short_history, "SPY": spy}
    top = compute_top30_universe(bars, as_of=as_of, size=2)
    assert top[0] == "AAA"  # steady dollar volume wins point-in-time
    assert "SPY" not in top  # ETF denylist despite max volume
    assert "NEWIPO" not in top  # < 60 qualifying bars
    assert "LOWV" != top[0]  # post-month-end volume explosion doesn't count


def test_top30_universe_ranks_by_dollar_volume() -> None:
    a = [dict(b, v=2_000_000) for b in _bars([50.0] * 60, end="2026-06-30")]  # $100M/d
    b = [dict(x, v=1_000_000) for x in _bars([300.0] * 60, end="2026-06-30")]  # $300M/d
    top = compute_top30_universe({"AAA": a, "BBB": b}, as_of="2026-06-30", size=2)
    assert top == ["BBB", "AAA"]


# --- signal predicates --------------------------------------------------------------------


def test_generals_dip_entry_predicate() -> None:
    up = [100.0 + i for i in range(57)]  # uptrend → close well above SMA50
    flush = up + [152.0, 149.0, 148.0]  # 3d return from 156 → -5.1%
    fired, ret3 = is_generals_dip_entry(flush)
    assert fired is True
    assert ret3 is not None and ret3 <= -3.5

    # 3d return not deep enough
    shallow = up + [155.5, 155.0, 154.0]
    assert is_generals_dip_entry(shallow)[0] is False

    # deep flush but close below SMA50 (downtrend)
    down = [200.0 - i for i in range(57)]
    down += [down[-1] * 0.99, down[-1] * 0.97, down[-1] * 0.94]
    assert is_generals_dip_entry(down)[0] is False

    # insufficient history: no 3d return at all, or a return but no SMA50
    assert is_generals_dip_entry([100.0, 96.0, 95.0]) == (False, None)
    assert is_generals_dip_entry([100.0, 96.0, 95.0, 94.0])[0] is False


def _dd_series():
    closes = [100.0] * 20 + [93.0, 92.0, 93.5]
    lows = [99.5] * 20 + [92.5, 91.5, 92.5]
    end = datetime(2026, 7, 3).date()
    n = len(closes)
    dates = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    return closes, lows, dates


def test_drawdown_stab_fires_on_first_qualifying_day() -> None:
    closes, lows, dates = _dd_series()
    fired, level = is_drawdown_stab_entry(closes, lows, dates, lambda d: 27.0)
    assert fired is True
    assert level == 100.0  # the 20d-high that defined the drawdown

    # a SECOND qualifying day in the same drawdown must not fire
    closes2 = closes + [94.5]
    lows2 = lows + [93.0]
    dates2 = dates + ["2026-07-04"]
    assert is_drawdown_stab_entry(closes2, lows2, dates2, lambda d: 27.0)[0] is False


def test_drawdown_stab_requires_vix_and_stabilization() -> None:
    closes, lows, dates = _dd_series()
    assert is_drawdown_stab_entry(closes, lows, dates, lambda d: 20.0)[0] is False  # VIX < 25
    assert is_drawdown_stab_entry(closes, lows, dates, lambda d: None)[0] is False  # no VIX

    # down day inside the drawdown is not a stabilization day
    down = closes[:-1] + [91.0]
    assert is_drawdown_stab_entry(down, lows, dates, lambda d: 27.0)[0] is False

    # no drawdown → never fires
    flat = [100.0] * 23
    assert is_drawdown_stab_entry(flat, [99.5] * 23, dates, lambda d: 27.0)[0] is False


def test_term_flip_predicate() -> None:
    assert is_term_flip_entry([1.05, 1.10, 0.98]) is True
    assert is_term_flip_entry([1.05, 1.10, 1.02]) is False  # not back under
    assert is_term_flip_entry([0.99, 1.10, 0.98]) is False  # only 1 inverted day
    assert is_term_flip_entry([1.02, 0.98]) is False  # insufficient history
    assert is_term_flip_entry([0.9, 0.9, 0.9]) is False  # never inverted


# --- ships disabled + Tier-B labeling ------------------------------------------------------


def test_sleeves_ship_disabled_propose_only(db: Path) -> None:
    seed_default_strategies(db_path=db)
    for sleeve_id in (GENERALS_ID, VOL_EVENT_ID):
        row = get_strategy(sleeve_id, db_path=db)
        assert row is not None, sleeve_id
        assert row["enabled"] is False
        assert row["account_id"] is None
        assert row["entry_mode"] == "propose"
        assert row["exit_mode"] == "propose"
    generals = get_strategy(GENERALS_ID, db_path=db)
    assert generals["max_concurrent_positions"] == 5
    assert generals["params"]["max_holding_days"] == 10
    assert generals["params"]["stop_pct"] is None
    assert generals["params"]["first_target_pct"] is None
    assert generals["params"]["trail_pct"] is None
    vol = get_strategy(VOL_EVENT_ID, db_path=db)
    assert vol["max_concurrent_positions"] == 1
    assert vol["params"]["triggers"] == {
        "drawdown_stab": {"enabled": True},
        "term_flip": {"enabled": True},
    }


def test_strategy_kind_and_exit_engine_wiring() -> None:
    # signals-side: NOT the generic dip kind → the generic strategy scan skips them.
    assert strategy_kind("generals_dip") == "generals_dip"
    assert strategy_kind("vol_event") == "vol_event"
    # trading-side (unchanged): generals_dip rides the dip evaluator (pure 10td time
    # cap via None price legs); vol_event is unknown → exit engine skips (sleeve
    # module manages its exits).
    assert exit_engine_kind("generals_dip") == "dip"
    assert exit_engine_kind("vol_event") is None


def test_tier_b_conviction_never_ships_win_prob(tmp_path: Path) -> None:
    for key in ("generals_dip", "drawdown_stab", "term_flip"):
        conviction = strategy_conviction(key)
        assert conviction is not None, key
        assert conviction["win_prob"] is None
        assert conviction["validated"] == "regime-local"
        assert conviction["sleeve"] == "regime_local"
        stats = conviction["regime_stats"]
        assert "caveats" in stats and "kill line armed" in stats["note"]

    # committed generals numbers (the honest pair: last-12m AND the full window)
    g = strategy_conviction("generals_dip")["regime_stats"]
    assert g["excess_ev_pct"] == 3.63 and g["ci"] == [0.11, 4.66] and g["n"] == 399
    assert "not significant" in g["full_window"]

    # a (mistaken) strategy_stats.json entry can NEVER override the Tier-B block
    stats_path = tmp_path / "strategy_stats.json"
    stats_path.write_text(
        json.dumps(
            {"strategies": {"generals_dip": {"win_rate": 0.9, "validated": "validated"}}}
        )
    )
    overridden = strategy_conviction("generals_dip", path=stats_path)
    assert overridden["win_prob"] is None
    assert overridden["validated"] == "regime-local"


# --- generals_dip scan ---------------------------------------------------------------------


def _enable_sleeve(db: Path, sleeve_id: str) -> None:
    seed_default_strategies(db_path=db)
    update_strategy(sleeve_id, enabled=True, db_path=db)


def _flush_bars() -> list[dict]:
    closes = [100.0 + i for i in range(57)] + [152.0, 149.0, 148.0]
    return _bars(closes)


def test_generals_scan_proposes_ranked_flushes(db: Path, scan_env, monkeypatch) -> None:
    _enable_sleeve(db, GENERALS_ID)
    mark(
        "regime:universe:generals_dip",
        "universe",
        payload={"month": "2026-07", "as_of": "2026-06-30", "symbols": ["AAA", "BBB", "CCC"]},
        db_path=db,
    )
    deep = _flush_bars()
    deeper_closes = [100.0 + i for i in range(57)] + [150.0, 145.0, 143.0]
    _patch_bars(
        monkeypatch,
        {
            "AAA": deep,
            "BBB": _bars([100.0] * 60),  # no flush
            "CCC": _bars(deeper_closes),  # more oversold → ranked first
            "SPY": _bars([100.0] * 60),
        },
    )

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    proposed = [a for a in acted if a["kind"] == "propose" and a["sleeve"] == GENERALS_ID]
    assert [p["symbol"] for p in proposed] == ["CCC", "AAA"]  # most-oversold first

    calls = [c for c in scan_env["proposals"] if c.get("rule_id") == GENERALS_ID]
    assert len(calls) == 2
    assert calls[0]["args"]["symbol"] == "CCC"
    assert calls[0]["args"]["side"] == "buy"
    assert calls[0]["source"] == f"strategy:{GENERALS_ID}"
    assert "Tier-B" in calls[0]["thesis"]

    # second pass the same day is deduped — no double proposals
    acted2 = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert [a for a in acted2 if a.get("kind") == "propose"] == []


def test_generals_scan_dormancy_says_why(db: Path, scan_env, monkeypatch) -> None:
    _enable_sleeve(db, GENERALS_ID)
    mark(
        "regime:universe:generals_dip",
        "universe",
        payload={"month": "2026-07", "as_of": "2026-06-30", "symbols": ["AAA"]},
        db_path=db,
    )
    _patch_bars(monkeypatch, {"AAA": _bars([100.0] * 60), "SPY": _bars([100.0] * 60)})

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert acted == []
    status = sleeves_status(db_path=db)
    assert "0 flushes in top-30 today" in status["sleeves"][GENERALS_ID]["last_scan"]["status"]
    # vol_event (still disabled) is visibly dormant too
    assert status["sleeves"][VOL_EVENT_ID]["last_scan"]["status"] == "disabled (not armed)"


def test_generals_scan_respects_concurrency_cap(db: Path, scan_env, monkeypatch) -> None:
    _enable_sleeve(db, GENERALS_ID)
    update_strategy(GENERALS_ID, max_concurrent_positions=1, db_path=db)
    mark(
        "regime:universe:generals_dip",
        "universe",
        payload={"month": "2026-07", "as_of": "2026-06-30", "symbols": ["AAA", "CCC"]},
        db_path=db,
    )
    record_open_position(
        account_id="acct_1", symbol="ZZZ", strategy_id=GENERALS_ID, qty="5",
        exit_mode="propose", params={}, db_path=db,
    )
    _patch_bars(monkeypatch, {"AAA": _flush_bars(), "CCC": _flush_bars()})

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert [a for a in acted if a.get("kind") == "propose"] == []
    assert scan_env["proposals"] == []
    assert "skipped 2" in sleeves_status(db_path=db)["sleeves"][GENERALS_ID]["last_scan"]["status"]


def test_scan_outside_post_close_window_is_noop(db: Path, scan_env) -> None:
    _enable_sleeve(db, GENERALS_ID)
    midday = datetime(2026, 7, 3, 12, 0, tzinfo=_ET)
    saturday = datetime(2026, 7, 4, 18, 0, tzinfo=_ET)
    assert asyncio.run(run_regime_sleeves_scan(db_path=db, now=midday)) == []
    assert asyncio.run(run_regime_sleeves_scan(db_path=db, now=saturday)) == []
    assert scan_env["proposals"] == []


# --- vol_event scan: triggers, cluster cap, dormancy ---------------------------------------


def _term_flip_regime_rows() -> list[tuple[str, str, float]]:
    # ratio 1.11, 1.11 then 0.96 on 07-01/02 close basis (FRED lags the eval date)
    return [
        ("VIXCLS", "2026-06-29", 30.0),
        ("VIXCLS", "2026-06-30", 30.0),
        ("VIXCLS", "2026-07-01", 25.0),
        ("vix3m", "2026-06-29", 27.0),
        ("vix3m", "2026-06-30", 27.0),
        ("vix3m", "2026-07-01", 26.0),
    ]


def test_vol_event_term_flip_proposes_and_tags(db: Path, scan_env, monkeypatch) -> None:
    _enable_sleeve(db, VOL_EVENT_ID)
    _seed_regime(db, _term_flip_regime_rows())
    _patch_bars(monkeypatch, {"SPY": _bars([100.0] * 40)})

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    proposed = [a for a in acted if a.get("kind") == "propose"]
    assert len(proposed) == 1 and proposed[0]["trigger_id"] == "term_flip"

    call = scan_env["proposals"][0]
    assert call["args"]["symbol"] == "SPY"
    assert call["rule_id"] == "term_flip"
    assert call["source"] == f"strategy:{VOL_EVENT_ID}"
    assert "sleeve=regime_local" in call["thesis"]
    # entry ctx stored for the exit evaluator
    ctx = get_payload(f"regime:ctx:{proposed[0]['proposal_id']}", db_path=db)
    assert ctx["trigger_id"] == "term_flip"


def test_vol_event_cluster_cap_counts_vix_spike_and_journals_suppression(
    db: Path, scan_env, monkeypatch
) -> None:
    _enable_sleeve(db, VOL_EVENT_ID)
    _seed_regime(db, _term_flip_regime_rows())
    _patch_bars(monkeypatch, {"SPY": _bars([100.0] * 40)})
    # an OPEN vix_spike position occupies the single vol-event slot
    record_open_position(
        account_id="acct_1", symbol="SPY", strategy_id="vix_spike", qty="10",
        exit_mode="auto", params={}, db_path=db,
    )

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert scan_env["proposals"] == []
    assert [a["kind"] for a in acted] == ["suppressed"]

    rows = by_strategy(VOL_EVENT_ID, db_path=db)
    suppressed = [r for r in rows if r.get("decision") == "suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["rule_id"] == "term_flip"
    assert "suppressed (cluster cap)" in suppressed[0]["thesis"]
    assert "trigger_id=term_flip" in suppressed[0]["thesis"]
    assert "suppressed (cluster cap)" in (
        sleeves_status(db_path=db)["sleeves"][VOL_EVENT_ID]["last_scan"]["status"]
    )


def test_vol_event_term_flip_dormant_when_vix3m_stale(
    db: Path, scan_env, monkeypatch
) -> None:
    _enable_sleeve(db, VOL_EVENT_ID)
    # ratio pattern would fire, but vix3m is ~2 weeks stale
    _seed_regime(
        db,
        [
            ("VIXCLS", "2026-06-16", 30.0),
            ("VIXCLS", "2026-06-17", 30.0),
            ("VIXCLS", "2026-06-18", 25.0),
            ("vix3m", "2026-06-16", 27.0),
            ("vix3m", "2026-06-17", 27.0),
            ("vix3m", "2026-06-18", 26.0),
        ],
    )
    _patch_bars(monkeypatch, {"SPY": _bars([100.0] * 40)})

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert scan_env["proposals"] == []
    assert [a for a in acted if a.get("kind") == "propose"] == []
    status = sleeves_status(db_path=db)["sleeves"][VOL_EVENT_ID]
    assert "term_flip dormant (vix3m stale)" in status["last_scan"]["status"]
    assert "no active vol episode" in status["last_scan"]["status"]
    assert status["vix3m_fresh"] is False


def test_vol_event_drawdown_stab_proposes_with_recovery_level(
    db: Path, scan_env, monkeypatch
) -> None:
    _enable_sleeve(db, VOL_EVENT_ID)
    closes, lows, _ = _dd_series()
    _patch_bars(monkeypatch, {"SPY": _bars(closes, lows=lows)})
    _seed_regime(db, [("VIXCLS", "2026-07-02", 28.0)])  # lagged FRED close, >= 25

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    proposed = [a for a in acted if a.get("kind") == "propose"]
    assert len(proposed) == 1 and proposed[0]["trigger_id"] == "drawdown_stab"
    ctx = get_payload(f"regime:ctx:{proposed[0]['proposal_id']}", db_path=db)
    assert ctx["recovery_level"] == 100.0


# --- vol_event exits -------------------------------------------------------------------


def _open_vol_position(
    db: Path, *, entry_days_ago: int, ctx: dict, exec_id: str = "ex_1"
) -> str:
    pid = record_open_position(
        account_id="acct_1", symbol="SPY", strategy_id=VOL_EVENT_ID, qty="10",
        exit_mode="propose", params={}, entry_execution_id=exec_id, db_path=db,
    )
    entry = (datetime.now(UTC) - timedelta(days=entry_days_ago)).isoformat()
    with connect(db) as conn:
        conn.execute("UPDATE managed_positions SET entry_date = ? WHERE id = ?", (entry, pid))
        conn.commit()
    mark(f"regime:ctx:p_{exec_id}", "entry_context", payload=ctx, db_path=db)
    return pid


@pytest.fixture
def exit_env(db: Path, scan_env, monkeypatch):
    monkeypatch.setattr(
        rs, "get_execution",
        lambda eid, *, db_path: {"proposal_id": f"p_{eid}"},
    )
    monkeypatch.setattr(rs, "get_account", lambda aid, *, db_path: {"broker": "alpaca"})
    monkeypatch.setattr(
        rs, "get_latest_snapshot",
        lambda broker, *, account_id, db_path: {
            "account": {"id": account_id},
            "positions": [{"symbol": "SPY", "qty": 10, "side": "long"}],
        },
    )
    return scan_env


def test_vol_event_term_flip_time_cap_exit(db: Path, exit_env) -> None:
    seed_default_strategies(db_path=db)
    strategy = get_strategy(VOL_EVENT_ID, db_path=db)
    pid = _open_vol_position(db, entry_days_ago=45, ctx={"trigger_id": "term_flip"})

    acted = asyncio.run(
        _vol_event_exits(strategy, _bars([100.0] * 40), _TODAY_ET, [], db_path=db)
    )
    assert len(acted) == 1 and acted[0]["reason"].startswith("time_cap_21")
    call = exit_env["proposals"][0]
    assert call["args"]["side"] == "sell" and call["args"]["qty"] == 10
    assert call["source"] == f"exit:{VOL_EVENT_ID}"
    assert call["score"] is False

    row = next(r for r in list_open_positions(db_path=db) if r["id"] == pid)
    marker = row["exit_context"]["pending_exit"]
    assert marker["kind"] == "propose" and marker["ref"] == "prop_1"

    # same-day re-run must not double-propose (marker throttle)
    acted2 = asyncio.run(
        _vol_event_exits(strategy, _bars([100.0] * 40), _TODAY_ET, [], db_path=db)
    )
    assert acted2 == [] and len(exit_env["proposals"]) == 1


def test_vol_event_drawdown_stab_recovery_exit(db: Path, exit_env) -> None:
    seed_default_strategies(db_path=db)
    strategy = get_strategy(VOL_EVENT_ID, db_path=db)
    _open_vol_position(
        db, entry_days_ago=3, ctx={"trigger_id": "drawdown_stab", "recovery_level": 100.0}
    )

    # below the recovery level and under the cap → hold
    acted = asyncio.run(
        _vol_event_exits(strategy, _bars([95.0] * 40), _EVAL_DATE, [], db_path=db)
    )
    assert acted == [] and exit_env["proposals"] == []

    # close recovers the 20d-high level → next-open sell proposal
    acted = asyncio.run(
        _vol_event_exits(strategy, _bars([101.0] * 40), _EVAL_DATE, [], db_path=db)
    )
    assert len(acted) == 1 and acted[0]["reason"] == "recovered_20d_high"


# --- kill lines -------------------------------------------------------------------------


def _journal_trade(
    db: Path,
    *,
    proposal_id: str,
    strategy_id: str,
    rule_id: str,
    entry: float,
    exit_price: float,
    created_at: str,
) -> None:
    record_decision(
        proposal_id=proposal_id, symbol="SPY", side="buy", qty="10",
        order_type="market", source=f"strategy:{strategy_id}", strategy_id=strategy_id,
        rule_id=rule_id, created_at=created_at, db_path=db,
    )
    set_entry_fill(
        proposal_id, entry_execution_id="e", entry_fill_price=str(entry),
        qty_filled="10", db_path=db,
    )
    resolve_outcome(
        proposal_id, exit_execution_id="x", exit_fill_price=str(exit_price),
        close_reason="test", entry_time=created_at,
        close_time=datetime.now(UTC).isoformat(), db_path=db,
    )


def _spy_flat_bars() -> list[dict]:
    return _bars([100.0] * 120)


def test_generals_kill_line_trips_on_negative_excess(
    db: Path, scan_env, monkeypatch
) -> None:
    _enable_sleeve(db, GENERALS_ID)
    _patch_bars(monkeypatch, {"SPY": _spy_flat_bars()})
    for i, exit_price in enumerate((95.0, 94.0)):  # losses vs flat SPY → excess < 0
        _journal_trade(
            db, proposal_id=f"g{i}", strategy_id=GENERALS_ID, rule_id=GENERALS_ID,
            entry=100.0, exit_price=exit_price, created_at="2026-06-15T14:00:00+00:00",
        )

    strategy = get_strategy(GENERALS_ID, db_path=db)
    tripped = asyncio.run(_generals_kill_check(strategy, _EVAL_DATE, db_path=db))
    assert tripped is True
    assert get_strategy(GENERALS_ID, db_path=db)["enabled"] is False

    rows = by_strategy(GENERALS_ID, db_path=db)
    fails = [r for r in rows if r.get("decision") == "kill_check_fail"]
    assert len(fails) == 1 and "FAIL" in fails[0]["thesis"]
    assert any("Kill line tripped" in n["title"] for n in scan_env["notifies"])

    # month is marked — a second check the same month is a no-op
    assert asyncio.run(_generals_kill_check(strategy, _EVAL_DATE, db_path=db)) is False


def test_generals_kill_line_passes_and_journals_with_no_data(
    db: Path, scan_env, monkeypatch
) -> None:
    _enable_sleeve(db, GENERALS_ID)
    _patch_bars(monkeypatch, {"SPY": _spy_flat_bars()})
    strategy = get_strategy(GENERALS_ID, db_path=db)
    tripped = asyncio.run(_generals_kill_check(strategy, _EVAL_DATE, db_path=db))
    assert tripped is False
    assert get_strategy(GENERALS_ID, db_path=db)["enabled"] is True
    rows = by_strategy(GENERALS_ID, db_path=db)
    passes = [r for r in rows if r.get("decision") == "kill_check_pass"]
    assert len(passes) == 1  # journaled either way
    assert scan_env["notifies"] == []


def test_generals_win_rate_leg_needs_full_60_trade_window(
    db: Path, scan_env, monkeypatch
) -> None:
    _enable_sleeve(db, GENERALS_ID)
    _patch_bars(monkeypatch, {"SPY": _spy_flat_bars()})
    # 10 trades at 40% win rate but POSITIVE mean excess: win-rate leg must not trip
    # below a full 60-trade window, and the excess leg is positive → PASS.
    for i in range(10):
        exit_price = 110.0 if i < 4 else 99.5
        _journal_trade(
            db, proposal_id=f"w{i}", strategy_id=GENERALS_ID, rule_id=GENERALS_ID,
            entry=100.0, exit_price=exit_price, created_at="2026-06-15T14:00:00+00:00",
        )
    strategy = get_strategy(GENERALS_ID, db_path=db)
    assert asyncio.run(_generals_kill_check(strategy, _EVAL_DATE, db_path=db)) is False
    assert get_strategy(GENERALS_ID, db_path=db)["enabled"] is True


def test_drawdown_stab_kill_on_three_consecutive_losses(db: Path, scan_env) -> None:
    seed_default_strategies(db_path=db)
    for i in range(3):
        _journal_trade(
            db, proposal_id=f"dd{i}", strategy_id=VOL_EVENT_ID, rule_id="drawdown_stab",
            entry=100.0, exit_price=98.0, created_at=f"2026-06-1{i + 1}T14:00:00+00:00",
        )
    asyncio.run(_vol_event_kill_checks(_spy_flat_bars(), db_path=db))

    vol = get_strategy(VOL_EVENT_ID, db_path=db)
    trig = vol["params"]["triggers"]["drawdown_stab"]
    assert trig["enabled"] is False
    assert "disabled_reason" in trig
    assert vol["params"]["triggers"]["term_flip"]["enabled"] is True  # untouched
    rows = by_strategy(VOL_EVENT_ID, db_path=db)
    assert any(r.get("decision") == "kill_check_fail" for r in rows)
    assert any("Kill line tripped" in n["title"] for n in scan_env["notifies"])


def test_drawdown_stab_kill_on_episode_mae(db: Path, scan_env, monkeypatch) -> None:
    seed_default_strategies(db_path=db)
    _journal_trade(
        db, proposal_id="mae1", strategy_id=VOL_EVENT_ID, rule_id="drawdown_stab",
        entry=100.0, exit_price=101.0, created_at="2026-06-15T14:00:00+00:00",
    )
    # SPY printed 80 (−20% vs the 100 entry) inside the episode window
    # (bars end 2026-07-03; the dip lands ~2026-06-19, after the 06-15 entry)
    closes = [100.0] * 105 + [80.0] + [100.0] * 14
    asyncio.run(_vol_event_kill_checks(_bars(closes), db_path=db))
    vol = get_strategy(VOL_EVENT_ID, db_path=db)
    assert vol["params"]["triggers"]["drawdown_stab"]["enabled"] is False


def test_term_flip_kill_needs_ten_episodes_then_trips(db: Path, scan_env) -> None:
    seed_default_strategies(db_path=db)
    # 5 weak episodes → not enough to trip
    for i in range(5):
        _journal_trade(
            db, proposal_id=f"tf{i}", strategy_id=VOL_EVENT_ID, rule_id="term_flip",
            entry=100.0, exit_price=100.5, created_at=f"2026-05-{i + 1:02d}T14:00:00+00:00",
        )
    asyncio.run(_vol_event_kill_checks(_spy_flat_bars(), db_path=db))
    vol = get_strategy(VOL_EVENT_ID, db_path=db)
    assert vol["params"]["triggers"]["term_flip"]["enabled"] is True
    rows = by_strategy(VOL_EVENT_ID, db_path=db)
    assert any(r.get("decision") == "kill_check_pass" for r in rows)

    # 5 more (10 total, mean +0.5% < +2.0%) → suspended
    for i in range(5, 10):
        _journal_trade(
            db, proposal_id=f"tf{i}", strategy_id=VOL_EVENT_ID, rule_id="term_flip",
            entry=100.0, exit_price=100.5, created_at=f"2026-05-{i + 1:02d}T14:00:00+00:00",
        )
    asyncio.run(_vol_event_kill_checks(_spy_flat_bars(), db_path=db))
    vol = get_strategy(VOL_EVENT_ID, db_path=db)
    assert vol["params"]["triggers"]["term_flip"]["enabled"] is False
    assert any("Kill line tripped" in n["title"] for n in scan_env["notifies"])


def test_disabled_trigger_stays_dormant_and_visible(db: Path, scan_env, monkeypatch) -> None:
    _enable_sleeve(db, VOL_EVENT_ID)
    vol = get_strategy(VOL_EVENT_ID, db_path=db)
    params = dict(vol["params"])
    params["triggers"] = {
        "drawdown_stab": {"enabled": False, "disabled_reason": "kill line"},
        "term_flip": {"enabled": True},
    }
    update_strategy(VOL_EVENT_ID, params=params, db_path=db)
    closes, lows, _ = _dd_series()
    _patch_bars(monkeypatch, {"SPY": _bars(closes, lows=lows)})
    _seed_regime(db, [("VIXCLS", "2026-07-02", 28.0)])

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert [a for a in acted if a.get("kind") == "propose"] == []
    status = sleeves_status(db_path=db)["sleeves"][VOL_EVENT_ID]
    assert "drawdown_stab disabled (kill line)" in status["last_scan"]["status"]
    assert status["triggers"]["drawdown_stab"]["enabled"] is False


# --- adversarial-review regression fixes (2026-07-03) --------------------------------------


def test_vol_event_exit_runs_while_sleeve_disabled(db: Path, exit_env, monkeypatch) -> None:
    """Reviewer repro (finding 1): sleeve DISABLED + open position way past its 21td
    cap must still get an exit proposal — exits are never gated on enabled."""
    seed_default_strategies(db_path=db)  # vol_event ships disabled
    assert get_strategy(VOL_EVENT_ID, db_path=db)["enabled"] is False
    _open_vol_position(db, entry_days_ago=69, ctx={"trigger_id": "term_flip"})
    _patch_bars(monkeypatch, {"SPY": _bars([100.0] * 40)})

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    exits = [a for a in acted if a["kind"] == "exit_propose"]
    assert len(exits) == 1 and exits[0]["reason"].startswith("time_cap_21")
    call = exit_env["proposals"][0]
    assert call["args"]["side"] == "sell" and call["source"] == f"exit:{VOL_EVENT_ID}"
    # entries stayed gated; the exit is visible next to the disabled status
    status = sleeves_status(db_path=db)["sleeves"][VOL_EVENT_ID]["last_scan"]["status"]
    assert "exit proposed" in status and "disabled (not armed)" in status


def test_vol_event_exit_runs_through_kill_switch_gate(db: Path, exit_env, monkeypatch) -> None:
    """Finding 1: kill switch / proposals-paused gate blocks ENTRIES only — an open
    position past its cap still gets its exit proposal."""
    seed_default_strategies(db_path=db)
    update_strategy(VOL_EVENT_ID, enabled=True, db_path=db)
    monkeypatch.setattr(rs, "is_kill_switch_active", lambda: True)
    _open_vol_position(db, entry_days_ago=69, ctx={"trigger_id": "term_flip"})
    _patch_bars(monkeypatch, {"SPY": _bars([100.0] * 40)})

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert [a["kind"] for a in acted] == ["exit_propose"]
    status = sleeves_status(db_path=db)["sleeves"][VOL_EVENT_ID]["last_scan"]["status"]
    assert "exit proposed" in status and "gated" in status


def test_vol_event_kill_checks_run_while_sleeve_disabled(
    db: Path, scan_env, monkeypatch
) -> None:
    """Finding 1: per-episode kill checks fire on newly resolved episodes even while
    the sleeve is disabled."""
    seed_default_strategies(db_path=db)  # vol_event ships disabled
    for i in range(3):
        _journal_trade(
            db, proposal_id=f"dd{i}", strategy_id=VOL_EVENT_ID, rule_id="drawdown_stab",
            entry=100.0, exit_price=98.0, created_at=f"2026-06-1{i + 1}T14:00:00+00:00",
        )
    _patch_bars(monkeypatch, {"SPY": _spy_flat_bars()})

    asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    vol = get_strategy(VOL_EVENT_ID, db_path=db)
    assert vol["params"]["triggers"]["drawdown_stab"]["enabled"] is False
    rows = by_strategy(VOL_EVENT_ID, db_path=db)
    assert any(r.get("decision") == "kill_check_fail" for r in rows)


def test_nan_close_candidate_does_not_abort_generals_pass(
    db: Path, scan_env, monkeypatch
) -> None:
    """Finding 2: a NaN close must be skipped at sizing (Decimal('nan') → floor raises
    → whole pass aborted), leaving the other valid candidates to propose."""
    _enable_sleeve(db, GENERALS_ID)
    mark(
        "regime:universe:generals_dip",
        "universe",
        payload={"month": "2026-07", "as_of": "2026-06-30", "symbols": ["NANCO", "AAA"]},
        db_path=db,
    )
    nan_bars = _bars([100.0] * 59 + [float("nan")])
    _patch_bars(
        monkeypatch,
        {"AAA": _flush_bars(), "NANCO": nan_bars, "SPY": _bars([100.0] * 60)},
    )
    # Force both through the predicate so the NaN close reaches sizing; NaN ranks
    # most-oversold so it is sized BEFORE the valid candidate.
    monkeypatch.setattr(
        rs, "is_generals_dip_entry",
        lambda cv, **kw: (True, -6.0 if cv[-1] != cv[-1] else -5.0),
    )

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    proposed = [a for a in acted if a.get("kind") == "propose"]
    assert [p["symbol"] for p in proposed] == ["AAA"]
    status = sleeves_status(db_path=db)["sleeves"][GENERALS_ID]["last_scan"]["status"]
    assert "proposed 1" in status and "skipped 1" in status


def _pending_vix_spike_buy(db: Path) -> dict:
    return create_pending_proposal(
        proposal={"symbol": "SPY", "side": "buy", "qty": 10, "order_type": "market"},
        intent_fields={"symbol": "SPY", "side": "buy", "qty": 10, "order_type": "market"},
        session_id=None,
        ttl_seconds=3600,
        summary="vix_spike entry",
        source="strategy:vix_spike",
        db_path=db,
    )


def test_cluster_cap_counts_pending_vix_spike_buy(db: Path, scan_env, monkeypatch) -> None:
    """Finding 3: an unapproved vix_spike BUY card (intraday scan) must occupy the
    single vol-event slot at the evening vol_event scan."""
    _enable_sleeve(db, VOL_EVENT_ID)
    _seed_regime(db, _term_flip_regime_rows())
    _patch_bars(monkeypatch, {"SPY": _bars([100.0] * 40)})
    _pending_vix_spike_buy(db)

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert scan_env["proposals"] == []
    assert [a["kind"] for a in acted] == ["suppressed"]


def test_cluster_cap_counts_approving_vix_spike_buy(db: Path, scan_env, monkeypatch) -> None:
    """Finding 3 + non-blocking #5: a proposal mid-approval ('approving') still
    occupies the slot — same guard the exit-side throttle already applies."""
    _enable_sleeve(db, VOL_EVENT_ID)
    _seed_regime(db, _term_flip_regime_rows())
    _patch_bars(monkeypatch, {"SPY": _bars([100.0] * 40)})
    row = _pending_vix_spike_buy(db)
    assert transition_proposal_status(
        row["id"], expect="pending_phone", to="approving", db_path=db
    )

    acted = asyncio.run(run_regime_sleeves_scan(db_path=db, now=_NOW))
    assert scan_env["proposals"] == []
    assert [a["kind"] for a in acted] == ["suppressed"]
