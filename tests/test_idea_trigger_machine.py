"""Armed-idea state machine: trigger fire, TTL expiry (counterfactual), TTL
computation, fire-before-expire race, restart (Fusion Horizon Redesign §2.5/§3/§4).

Driven by synthetic dots, injected price/ATR caches, and a fixed ``now``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from db.base import connect
from db.fusion import init_fusion_tables, put_atr
from db.idea_outcomes import init_idea_outcomes_tables, register_idea
from signals.fusion.lifecycle import compute_trigger_expiry, sweep_lifecycle

_UTC = timezone.utc


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    init_fusion_tables(path)
    init_idea_outcomes_tables(path)
    return path


def _open_episode(db: Path, episode_id: str, symbol: str, opened: str) -> None:
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at, state) "
            "VALUES (?, ?, ?, ?, ?, 'open')",
            (episode_id, symbol, opened[:10], opened, opened),
        )
        conn.commit()


def _arm(
    db: Path,
    *,
    symbol: str,
    episode_id: str,
    trigger: dict,
    armed_at: str,
    expires: str,
    direction: str = "long",
) -> int:
    idea_id = register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol=symbol,
        direction=direction,
        entry_zone={"low": "100", "high": "100"},
        stop="95" if direction == "long" else "105",
        targets=["110"] if direction == "long" else ["90"],
        plan_horizon="9d",
        as_of=armed_at,
        episode_id=episode_id,
        db_path=db,
    )
    assert idea_id is not None
    with connect(db) as conn:
        conn.execute(
            "UPDATE idea_outcomes SET lifecycle_state='armed', armed_at=?, "
            "trigger_json=?, trigger_expires_at=? WHERE id=?",
            (armed_at, json.dumps(trigger), expires, idea_id),
        )
        conn.commit()
    return idea_id


def _idea(db: Path, idea_id: int) -> dict:
    with connect(db) as conn:
        return dict(
            conn.execute(
                "SELECT status, lifecycle_state, exit_reason, fill_status, "
                "ret_pct, spy_ret_pct, alpha_pct FROM idea_outcomes WHERE id=?",
                (idea_id,),
            ).fetchone()
        )


def _keep_open(monkeypatch) -> None:
    """Pin the decay floor to 0 so the underlying episode never decay-closes —
    isolates the armed-idea axis from the episode-close axis."""
    monkeypatch.setattr("signals.fusion.lifecycle.FUSION_EPISODE_DECAY_FLOOR", 0.0)


# ------------------------------------------------------------------ trigger fires
def test_level_touch_fires_trigger(db: Path, monkeypatch) -> None:
    _keep_open(monkeypatch)
    _open_episode(db, "E", "AAA", "2026-07-20T14:00:00+00:00")
    put_atr("AAA", "2026-07-19", Decimal("5"), db_path=db)
    trigger = {
        "class": "impulse",
        "ttl": {"unit": "hours", "value": 4},
        "condition": {"type": "level_touch", "level": "101.0", "direction": "bull", "threshold_atr": 0.1},
    }
    idea_id = _arm(db, symbol="AAA", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-20T18:00:00+00:00")
    # price 101.05 is within 0.1×5=0.5 of the 101 level → fires.
    counts = sweep_lifecycle(datetime(2026, 7, 20, 15, 0, tzinfo=_UTC), db,
                             price_lookup=lambda _s: Decimal("101.05"))
    assert counts["ideas_trigger_fired"] == 1
    row = _idea(db, idea_id)
    assert row["lifecycle_state"] == "trigger_fired"
    assert row["status"] == "pending"  # mechanical axis unchanged; no proposal here


def test_level_touch_does_not_fire_when_far(db: Path, monkeypatch) -> None:
    _keep_open(monkeypatch)
    _open_episode(db, "E", "AAA", "2026-07-20T14:00:00+00:00")
    put_atr("AAA", "2026-07-19", Decimal("5"), db_path=db)
    trigger = {
        "class": "impulse", "ttl": {"unit": "hours", "value": 4},
        "condition": {"type": "level_touch", "level": "101.0", "direction": "bull", "threshold_atr": 0.1},
    }
    idea_id = _arm(db, symbol="AAA", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-20T18:00:00+00:00")
    counts = sweep_lifecycle(datetime(2026, 7, 20, 15, 0, tzinfo=_UTC), db,
                             price_lookup=lambda _s: Decimal("103.0"))  # >0.5 away
    assert counts["ideas_trigger_fired"] == 0
    assert _idea(db, idea_id)["lifecycle_state"] == "armed"


def test_level_reclaim_fires_trigger(db: Path, monkeypatch) -> None:
    _keep_open(monkeypatch)
    _open_episode(db, "E", "BBB", "2026-07-20T14:00:00+00:00")
    trigger = {
        "class": "thesis_confirmation", "ttl": {"unit": "sessions", "value": 3},
        "condition": {"type": "level_reclaim", "level": "212.40", "direction": "bull"},
    }
    idea_id = _arm(db, symbol="BBB", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-23T20:00:00+00:00")
    # price closes back above the level → bull reclaim fires.
    counts = sweep_lifecycle(datetime(2026, 7, 21, 15, 0, tzinfo=_UTC), db,
                             price_lookup=lambda _s: Decimal("213.10"))
    assert counts["ideas_trigger_fired"] == 1
    assert _idea(db, idea_id)["lifecycle_state"] == "trigger_fired"


# ----------------------------------------------------------- TTL expiry → grade
def _split_fetch(symbol_bars, spy_bars):
    def _fx(symbol, since):
        return spy_bars if symbol == "SPY" else symbol_bars
    return _fx


def test_expired_ttl_records_counterfactual_grade(db: Path, monkeypatch) -> None:
    _keep_open(monkeypatch)
    _open_episode(db, "E", "EEE", "2026-07-06T14:00:00+00:00")
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4},
               "condition": {"type": "level_touch", "level": "999", "direction": "bull"}}
    idea_id = _arm(db, symbol="EEE", episode_id="E", trigger=trigger,
                   armed_at="2026-07-06T14:00:00+00:00", expires="2026-07-06T18:00:00+00:00")
    symbol_bars = [
        SimpleNamespace(time=f"2026-07-0{d}", low=100 + d, high=101 + d, close=100.5 + d, open=100 + d)
        for d in range(7, 10)
    ]
    spy_bars = [
        SimpleNamespace(time=f"2026-07-0{d}", low=400 + d, high=401 + d, close=400.5 + d, open=400 + d)
        for d in range(7, 10)
    ]
    fx = _split_fetch(symbol_bars, spy_bars)
    now = datetime(2026, 7, 9, 20, 0, tzinfo=_UTC)  # well past the 4h TTL
    with patch("research.idea_tracker.fetch_daily_bars_since", side_effect=fx):
        counts = sweep_lifecycle(now, db)
    assert counts["ideas_expired"] == 1
    row = _idea(db, idea_id)
    assert row["status"] == "expired"
    assert row["lifecycle_state"] == "expired"
    assert row["exit_reason"] == "trigger_ttl"
    assert row["fill_status"] == "counterfactual"
    # Benchmark leg stamped (expired ∈ _GRADED_STATUSES, fill_status != no_fill).
    assert row["ret_pct"] is not None
    assert row["spy_ret_pct"] is not None
    assert row["alpha_pct"] is not None


# ---------------------------------------------------------------- TTL computation
def test_impulse_ttl_is_wall_clock_hours() -> None:
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4}}
    expires = compute_trigger_expiry("2026-07-20T14:00:00+00:00", trigger)
    assert datetime.fromisoformat(expires) == datetime(2026, 7, 20, 18, 0, tzinfo=_UTC)


def test_thesis_ttl_is_trading_days_to_session_close() -> None:
    # Armed Mon 07-20 → 3 trading days = 07-23 (Thu), expiring 16:00 ET.
    trigger = {"class": "thesis_confirmation", "ttl": {"unit": "sessions", "value": 3}}
    expires = compute_trigger_expiry("2026-07-20T14:00:00+00:00", trigger)
    parsed = datetime.fromisoformat(expires)
    from trading.market_hours import _ET

    assert parsed == datetime(2026, 7, 23, 16, 0, tzinfo=_ET)


def test_thesis_ttl_skips_weekend() -> None:
    # Armed Fri 07-17 → 3 trading days = Wed 07-22 (Sat/Sun skipped).
    trigger = {"class": "thesis_confirmation", "ttl": {"unit": "sessions", "value": 3}}
    expires = compute_trigger_expiry("2026-07-17T14:00:00+00:00", trigger)
    assert datetime.fromisoformat(expires).date().isoformat() == "2026-07-22"


# ---------------------------------------------------------- fire-before-expire
def test_fire_wins_when_trigger_and_ttl_lapse_on_same_pass(db: Path, monkeypatch) -> None:
    _keep_open(monkeypatch)
    _open_episode(db, "E", "AAA", "2026-07-20T14:00:00+00:00")
    put_atr("AAA", "2026-07-19", Decimal("5"), db_path=db)
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4},
               "condition": {"type": "level_touch", "level": "101.0", "direction": "bull", "threshold_atr": 0.1}}
    # TTL already lapsed (expires in the past) AND the price satisfies the touch.
    idea_id = _arm(db, symbol="AAA", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-20T14:30:00+00:00")
    counts = sweep_lifecycle(datetime(2026, 7, 20, 15, 0, tzinfo=_UTC), db,
                             price_lookup=lambda _s: Decimal("101.0"))
    assert counts["ideas_trigger_fired"] == 1
    assert counts["ideas_expired"] == 0
    assert _idea(db, idea_id)["lifecycle_state"] == "trigger_fired"


# ----------------------------------------------------------- invalidation cascade
def test_closed_episode_invalidates_armed_idea_same_pass(db: Path) -> None:
    # An open episode with an adverse anchor closes price_invalidation this pass,
    # and the armed idea beneath it invalidates in the SAME pass (no benchmark).
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at, "
            "state, anchor_price, anchor_atr14, anchor_direction) "
            "VALUES ('E','FFF','2026-07-20','2026-07-20T14:00:00+00:00',"
            "'2026-07-20T14:00:00+00:00','open','100','5','bull')"
        )
        conn.commit()
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4},
               "condition": {"type": "level_touch", "level": "99", "direction": "bull"}}
    idea_id = _arm(db, symbol="FFF", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-25T20:00:00+00:00")
    counts = sweep_lifecycle(datetime(2026, 7, 20, 16, 0, tzinfo=_UTC), db,
                             price_lookup=lambda _s: Decimal("80.0"))
    assert counts["episodes_closed"] == 1
    assert counts["ideas_invalidated"] == 1
    row = _idea(db, idea_id)
    assert row["status"] == "invalidated"
    assert row["lifecycle_state"] == "invalidated"
    assert row["alpha_pct"] is None  # invalidated ∉ _GRADED_STATUSES → no benchmark leg


def test_aged_out_close_expires_armed_idea_as_counterfactual(db: Path, monkeypatch) -> None:
    """A dead-but-unbroken thesis (hard_ttl / decay close) must NOT censor an
    armed idea into `invalidated` — it runs out its trigger TTL into a COUNTED
    counterfactual (benchmark leg stamped). Only price_invalidation censors."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at, "
            "state, closed_at, close_reason) "
            "VALUES ('E','EEE','2026-07-06','2026-07-06T14:00:00+00:00',"
            "'2026-07-08T20:00:00+00:00','closed','2026-07-08T20:00:00+00:00','hard_ttl')"
        )
        conn.commit()
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4},
               "condition": {"type": "level_touch", "level": "999", "direction": "bull"}}
    idea_id = _arm(db, symbol="EEE", episode_id="E", trigger=trigger,
                   armed_at="2026-07-06T14:00:00+00:00", expires="2026-07-06T18:00:00+00:00")
    symbol_bars = [
        SimpleNamespace(time=f"2026-07-0{d}", low=100 + d, high=101 + d, close=100.5 + d, open=100 + d)
        for d in range(7, 10)
    ]
    spy_bars = [
        SimpleNamespace(time=f"2026-07-0{d}", low=400 + d, high=401 + d, close=400.5 + d, open=400 + d)
        for d in range(7, 10)
    ]
    fx = _split_fetch(symbol_bars, spy_bars)
    with patch("research.idea_tracker.fetch_daily_bars_since", side_effect=fx):
        counts = sweep_lifecycle(datetime(2026, 7, 9, 20, 0, tzinfo=_UTC), db)
    assert counts["ideas_invalidated"] == 0
    assert counts["ideas_expired"] == 1
    row = _idea(db, idea_id)
    assert row["status"] == "expired"
    assert row["fill_status"] == "counterfactual"
    assert row["alpha_pct"] is not None  # counted, not censored


def test_aged_out_close_leaves_born_idea_for_mechanical_grader(db: Path) -> None:
    """A born (never-armed) idea on an aged-out episode rides on to its
    mechanical grade — the calibration ledger counts everything it can."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at, "
            "state, closed_at, close_reason) "
            "VALUES ('E','BBB','2026-07-20','2026-07-20T14:00:00+00:00',"
            "'2026-07-21T20:00:00+00:00','closed','2026-07-21T20:00:00+00:00','decay_to_noise')"
        )
        conn.commit()
    idea_id = register_idea(
        origin="reason_v1", signal_family="fusion", symbol="BBB",
        entry_zone={"low": "100", "high": "100"}, stop="95", targets=["110"],
        plan_horizon="9d", as_of="2026-07-20T14:00:00+00:00", episode_id="E",
        lifecycle_state="born", db_path=db,
    )
    assert idea_id is not None
    counts = sweep_lifecycle(datetime(2026, 7, 22, 15, 0, tzinfo=_UTC), db)
    assert counts["ideas_invalidated"] == 0
    row = _idea(db, idea_id)
    assert row["status"] == "pending"
    assert row["lifecycle_state"] == "born"


# ------------------------------------------------- two-axis reconciliation (mirror)
def test_mechanically_resolved_row_retires_untouched(db: Path) -> None:
    """Review finding 1: the mechanical grader resolved this armed row `won`
    before its episode price-invalidated. The sweep must NEVER rewrite the
    outcome — it mirrors the row out of the armed cohort (lifecycle 'closed')
    with every grade field intact."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at, "
            "state, closed_at, close_reason) "
            "VALUES ('E','WON','2026-07-20','2026-07-20T14:00:00+00:00',"
            "'2026-07-21T20:00:00+00:00','closed','2026-07-21T20:00:00+00:00','price_invalidation')"
        )
        conn.commit()
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4},
               "condition": {"type": "level_touch", "level": "101", "direction": "bull"}}
    idea_id = _arm(db, symbol="WON", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-20T18:00:00+00:00")
    from db.idea_outcomes import update_outcome

    assert update_outcome(idea_id, status="won", ret_pct=9.0, alpha_pct=4.0, db_path=db)
    counts = sweep_lifecycle(datetime(2026, 7, 22, 15, 0, tzinfo=_UTC), db)
    assert counts["ideas_invalidated"] == 0
    assert counts["ideas_expired"] == 0
    row = _idea(db, idea_id)
    assert row["status"] == "won"
    assert row["ret_pct"] == 9.0
    assert row["alpha_pct"] == 4.0
    assert row["lifecycle_state"] == "closed"
    # And it has left the sweep cohort for good.
    counts = sweep_lifecycle(datetime(2026, 7, 22, 16, 0, tzinfo=_UTC), db)
    assert counts == {"episodes_closed": 0, "ideas_trigger_fired": 0,
                      "ideas_expired": 0, "ideas_invalidated": 0}


def test_entry_touched_row_promotes_to_live_never_counterfactual(db: Path, monkeypatch) -> None:
    """status='triggered' (grader: entry zone touched) mirrors to lifecycle
    'live' — a live idea is neither cascade- nor TTL-eligible (§3/§5)."""
    _keep_open(monkeypatch)
    _open_episode(db, "E", "LLL", "2026-07-20T14:00:00+00:00")
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4},
               "condition": {"type": "level_touch", "level": "999", "direction": "bull"}}
    idea_id = _arm(db, symbol="LLL", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-20T18:00:00+00:00")
    from db.idea_outcomes import update_outcome

    assert update_outcome(idea_id, status="triggered", db_path=db)
    # TTL long lapsed — but the touched row must promote, not expire.
    counts = sweep_lifecycle(datetime(2026, 7, 21, 15, 0, tzinfo=_UTC), db)
    assert counts["ideas_expired"] == 0
    row = _idea(db, idea_id)
    assert row["status"] == "triggered"
    assert row["lifecycle_state"] == "live"
    assert row["fill_status"] is None


# ----------------------------------------------------------------- restart safety
def test_restart_mid_armed_resumes_from_db(db: Path, monkeypatch) -> None:
    _keep_open(monkeypatch)
    _open_episode(db, "E", "AAA", "2026-07-20T14:00:00+00:00")
    put_atr("AAA", "2026-07-19", Decimal("5"), db_path=db)
    trigger = {"class": "impulse", "ttl": {"unit": "hours", "value": 4},
               "condition": {"type": "level_touch", "level": "101.0", "direction": "bull", "threshold_atr": 0.1}}
    idea_id = _arm(db, symbol="AAA", episode_id="E", trigger=trigger,
                   armed_at="2026-07-20T14:00:00+00:00", expires="2026-07-20T18:00:00+00:00")
    now = datetime(2026, 7, 20, 15, 0, tzinfo=_UTC)

    # First pass: price far → stays armed (no memory carried).
    sweep_lifecycle(now, db, price_lookup=lambda _s: Decimal("104.0"))
    assert _idea(db, idea_id)["lifecycle_state"] == "armed"

    # "Restart" = a fresh sweep call (new connections) resumes evaluation; price
    # now satisfies the touch → fires.
    counts = sweep_lifecycle(now, db, price_lookup=lambda _s: Decimal("101.0"))
    assert counts["ideas_trigger_fired"] == 1
    assert _idea(db, idea_id)["lifecycle_state"] == "trigger_fired"
