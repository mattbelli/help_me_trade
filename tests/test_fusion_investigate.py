from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config.idea_engine import REASON_DAILY_BUDGET, REASON_OPENING_RESERVE_FRAC
from db import lane_stats
from db.base import connect
from db.fusion import init_fusion_tables
from signals.fusion.investigate import _baseline, evaluate_investigation

_ET = ZoneInfo("America/New_York")
TODAY = "2026-07-20"  # a Monday trading day; all seeded baselines precede it
ARCH = ["options_actor", "structure"]


@pytest.fixture
def inv_db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    return path


def _seed(
    db: Path,
    episode_id: str,
    market_day: str,
    score: float,
    groups: list[str],
    *,
    contested: int = 0,
) -> None:
    """Persist a past-day episode + its day-final score_state (the pool source)."""
    ts = f"{market_day}T14:00:00+00:00"
    state = {
        "score": score,
        "group_scores": {g: score for g in groups},
        "contested": bool(contested),
    }
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, score, contested, signature
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                "S" + episode_id,
                market_day,
                ts,
                ts,
                float(score),
                int(contested),
                ",".join(sorted(groups)),
            ),
        )
        conn.execute(
            """
            INSERT INTO fusion_score_history(episode_id, score, score_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (episode_id, float(score), json.dumps(state), ts),
        )
        conn.commit()


def _live(
    episode_id: str,
    market_day: str,
    score: float,
    groups: list[str],
    *,
    contested: bool = False,
) -> tuple[dict, dict]:
    episode = {"id": episode_id, "symbol": "S" + episode_id, "market_day": market_day}
    state = {
        "score": score,
        "group_scores": {g: score for g in groups},
        "contested": contested,
        "direction_state": "contested" if contested else "aligned(bull)",
    }
    return episode, state


def _seed_healthy_baseline(db: Path) -> None:
    """20 archetype non-contested episodes @ score 1.0 → has_baseline, threshold 1.0."""
    days = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16"]
    for k in range(20):
        _seed(db, f"base{k}", days[k % 4], 1.0, ARCH)


def _shadow_count(db: Path) -> int:
    with connect(db) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM fusion_investigations "
                "WHERE decision = 'shadow_investigate'"
            ).fetchone()[0]
        )


def _meter(db: Path) -> float:
    return lane_stats.day("fusion_investigate", TODAY, db_path=db).get(
        "fusion_would_investigate", 0.0
    )


def _inside_window() -> datetime:
    return datetime(2026, 7, 20, 9, 45, tzinfo=_ET)  # 09:45 ET → inside open window


def _after_window() -> datetime:
    return datetime(2026, 7, 20, 10, 15, tzinfo=_ET)  # 10:15 ET → full budget unlocked


def test_threshold_is_the_b_times_s_order_statistic(inv_db: Path) -> None:
    # 7 distinct past days × 6 archetype episodes = 42; scores strictly increasing
    # by 0.5. S=7, B=5 → rank 35; the 35th-highest of 42 is scores[7] = 4.5.
    days = [
        "2026-07-06",
        "2026-07-07",
        "2026-07-08",
        "2026-07-09",
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
    ]
    scores = [round(1.0 + k * 0.5, 2) for k in range(42)]
    for k, score in enumerate(scores):
        _seed(inv_db, f"ep{k}", days[k % 7], score, ARCH)

    base = _baseline(TODAY, db_path=inv_db)
    assert base["has_baseline"] is True
    assert base["widened"] is False
    assert base["sessions"] == 7
    assert base["pool_size"] == 42
    assert base["threshold"] == sorted(scores, reverse=True)[34] == 4.5


def test_cold_start_widens_to_all_non_contested(inv_db: Path) -> None:
    days = ["2026-07-13", "2026-07-14", "2026-07-15"]
    for k in range(5):  # too few archetype episodes to stand alone
        _seed(inv_db, f"arch{k}", days[k % 3], 2.0, ARCH)
    for k in range(20):  # ...but the widened non-contested pool clears the floor
        _seed(inv_db, f"opt{k}", days[k % 3], 1.0, ["options_actor"])

    base = _baseline(TODAY, db_path=inv_db)
    assert base["has_baseline"] is True
    assert base["widened"] is True
    assert base["pool_size"] == 25


def test_no_baseline_below_floor_is_counted_not_bumped(inv_db: Path) -> None:
    for k in range(10):  # both archetype and widened pools stay under the floor
        _seed(inv_db, f"opt{k}", "2026-07-14", 1.0, ARCH)

    assert _baseline(TODAY, db_path=inv_db)["has_baseline"] is False

    episode, state = _live("live1", TODAY, 3.0, ARCH)
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        == "no_baseline"
    )
    with connect(inv_db) as conn:
        row = conn.execute(
            "SELECT decision, threshold FROM fusion_investigations WHERE episode_id = 'live1'"
        ).fetchone()
    assert row["decision"] == "no_baseline"
    assert row["threshold"] is None
    assert _meter(inv_db) == 0.0  # no_baseline never spends budget


def test_archetype_filter_requires_options_and_structure(inv_db: Path) -> None:
    _seed_healthy_baseline(inv_db)
    now = _after_window()

    options_only = _live("opts_only", TODAY, 5.0, ["options_actor"])
    assert evaluate_investigation(*options_only, db_path=inv_db, now=now) is None

    archetype = _live("arch", TODAY, 5.0, ARCH)
    assert (
        evaluate_investigation(*archetype, db_path=inv_db, now=now) == "shadow_investigate"
    )

    with connect(inv_db) as conn:
        ids = [
            r[0] for r in conn.execute("SELECT episode_id FROM fusion_investigations")
        ]
    assert ids == ["arch"]  # the options-only stack was never a candidate


def test_contested_stack_is_not_a_candidate(inv_db: Path) -> None:
    _seed_healthy_baseline(inv_db)
    episode, state = _live("cont", TODAY, 5.0, ARCH, contested=True)
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        is None
    )
    with connect(inv_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_investigations").fetchone()[0] == 0


def test_opening_reserve_defers_third_then_upgrades(inv_db: Path) -> None:
    _seed_healthy_baseline(inv_db)
    inside = _inside_window()

    d1 = evaluate_investigation(*_live("e1", TODAY, 5.0, ARCH), db_path=inv_db, now=inside)
    d2 = evaluate_investigation(*_live("e2", TODAY, 5.0, ARCH), db_path=inv_db, now=inside)
    episode3, state3 = _live("e3", TODAY, 5.0, ARCH)
    d3 = evaluate_investigation(episode3, state3, db_path=inv_db, now=inside)
    assert (d1, d2, d3) == (
        "shadow_investigate",
        "shadow_investigate",
        "reserve_deferred",
    )

    # After the window the deferred stack re-fires and upgrades in place.
    d3_after = evaluate_investigation(
        episode3, state3, db_path=inv_db, now=_after_window()
    )
    assert d3_after == "shadow_investigate"
    assert _shadow_count(inv_db) == 3
    assert _meter(inv_db) == 3.0

    with connect(inv_db) as conn:  # upgrade replaced the row, never added one
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM fusion_investigations WHERE episode_id = 'e3'"
            ).fetchone()[0]
            == 1
        )


def test_daily_budget_exhaustion_drops(inv_db: Path) -> None:
    _seed_healthy_baseline(inv_db)
    after = _after_window()
    for k in range(REASON_DAILY_BUDGET):
        assert (
            evaluate_investigation(*_live(f"e{k}", TODAY, 5.0, ARCH), db_path=inv_db, now=after)
            == "shadow_investigate"
        )
    assert (
        evaluate_investigation(*_live("over", TODAY, 5.0, ARCH), db_path=inv_db, now=after)
        == "dropped_budget"
    )
    assert _shadow_count(inv_db) == REASON_DAILY_BUDGET
    assert _meter(inv_db) == float(REASON_DAILY_BUDGET)


def test_shadow_investigate_is_sticky_and_bumps_once(inv_db: Path) -> None:
    _seed_healthy_baseline(inv_db)
    episode, state = _live("sticky", TODAY, 5.0, ARCH)
    after = _after_window()
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=after)
        == "shadow_investigate"
    )
    for _ in range(4):  # re-fires stay terminal, never a second row or bump
        assert (
            evaluate_investigation(episode, state, db_path=inv_db, now=after)
            == "shadow_investigate"
        )
    assert _shadow_count(inv_db) == 1
    assert _meter(inv_db) == 1.0


def test_opening_reserve_never_exceeds_daily_budget() -> None:
    assert math.ceil(REASON_OPENING_RESERVE_FRAC * REASON_DAILY_BUDGET) <= REASON_DAILY_BUDGET
