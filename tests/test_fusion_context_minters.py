"""Context/catalyst minters.

Board card fusion-unminted-dots: earnings_proximity (catalyst, SCORED latest/best
on structure's [0, 1] closeness scale). Round B (card #302) deleted the per-name
regime and index_pressure stamps — day-scoped context lives on macro.db. These
tests pin the earnings adjacency windows, the annotate-only invariant, and that
intake no longer mints market-wide facts onto every name.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from db.base import connect
from db.chart_features import init_chart_features
from db.earnings_calendar import upsert_earnings
from db.fusion import init_fusion_tables, put_atr
from db.index_pressure import upsert_minutes
from db.lane_stats import day as lane_day
from db.lane_stats import et_today
from db.options_flow import insert_event as insert_flow
from db.regime import upsert_regime
from signals.fusion.intake import intake_once

MARKET_DAY = "2026-07-17"  # a Friday
EVENT_TS = "2026-07-17T14:30:00+00:00"


@pytest.fixture
def fusion_db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    init_chart_features(path)
    return path


def _flow(event_id: str, *, symbol: str = "TSLA", ts: str = EVENT_TS) -> dict:
    return {
        "id": event_id,
        "ts": ts,
        "underlying": symbol,
        "occ_symbol": f"{symbol}260821C00300000",
        "right": "call",
        "event_type": "sweep",
        "premium_usd": 1_000_000,
        "direction": "buy_side",
        "created_at": ts,
    }


def _run_intake(db: Path) -> dict:
    return intake_once(db_path=db, price_lookup=lambda _s: Decimal("320.25"))


def _episode_id(db: Path, symbol: str) -> str:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT id FROM fusion_episodes WHERE symbol = ?", (symbol,)
        ).fetchone()
    assert row is not None, f"no episode for {symbol}"
    return str(row["id"])


def _dots(db: Path, episode_id: str, component: str) -> list[dict]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM evidence_dots WHERE episode_id = ? AND component = ?",
            (episode_id, component),
        ).fetchall()
    return [dict(r) for r in rows]


def _group_scores(db: Path, episode_id: str) -> dict:
    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT score_json FROM fusion_score_history
            WHERE episode_id = ? ORDER BY id DESC LIMIT 1
            """,
            (episode_id,),
        ).fetchone()
    assert row is not None
    return json.loads(row["score_json"])["group_scores"]


def _meter(db: Path, counter: str) -> float:
    return lane_day("fusion_intake", et_today(), db_path=db).get(counter, 0.0)


def _seed_actor(db: Path, *, symbol: str = "TSLA") -> None:
    put_atr(symbol, "2026-07-16", Decimal("12.50"), db_path=db)
    insert_flow(_flow(f"flow-{symbol}", symbol=symbol), db)


# ---------------------------------------------------------------- earnings ----


def test_upcoming_earnings_next_weekday_scores_full_points(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    upsert_earnings([{"symbol": "TSLA", "date": "2026-07-20"}], db_path=fusion_db)  # Monday

    _run_intake(fusion_db)

    ep = _episode_id(fusion_db, "TSLA")
    dots = _dots(fusion_db, ep, "earnings_proximity")
    assert len(dots) == 1
    dot = dots[0]
    assert dot["actor_group"] == "catalyst"
    assert dot["dot_kind"] == "state"
    assert dot["direction"] == "none"
    assert float(dot["points"]) == 1.0
    detail = json.loads(dot["detail_json"])
    assert detail["side"] == "upcoming"
    assert detail["weekdays"] == 1

    groups = _group_scores(fusion_db, ep)
    assert groups["catalyst"] == 1.0
    assert _meter(fusion_db, "earnings_dots") == 1.0


def test_earnings_at_window_edge_scores_low_and_beyond_mints_nothing(
    fusion_db: Path,
) -> None:
    _seed_actor(fusion_db)
    # 5 weekdays out (2026-07-24, Friday) = the FUSION_EARNINGS_AHEAD_TD edge.
    upsert_earnings([{"symbol": "TSLA", "date": "2026-07-24"}], db_path=fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db, "TSLA")
    (dot,) = _dots(fusion_db, ep, "earnings_proximity")
    assert float(dot["points"]) == pytest.approx(0.2)

    with connect(fusion_db) as conn:
        conn.execute("DELETE FROM earnings_calendar")
        conn.commit()
    upsert_earnings([{"symbol": "MSFT", "date": "2026-07-27"}], db_path=fusion_db)  # 6 weekdays
    put_atr("MSFT", "2026-07-16", Decimal("8.00"), db_path=fusion_db)
    insert_flow(_flow("flow-msft", symbol="MSFT"), fusion_db)
    _run_intake(fusion_db)
    ep2 = _episode_id(fusion_db, "MSFT")
    assert _dots(fusion_db, ep2, "earnings_proximity") == []


def test_reported_earnings_today_wins_over_far_upcoming(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    upsert_earnings(
        [
            {"symbol": "TSLA", "date": MARKET_DAY},  # printed this market day
            {"symbol": "TSLA", "date": "2026-07-23"},  # also 4 weekdays out
        ],
        db_path=fusion_db,
    )
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db, "TSLA")
    (dot,) = _dots(fusion_db, ep, "earnings_proximity")
    detail = json.loads(dot["detail_json"])
    assert detail["side"] == "today"
    assert float(dot["points"]) == 1.0


def test_earnings_dot_zeroes_when_window_passes(fusion_db: Path) -> None:
    # Review finding: a frozen last-good dot would lie to the dossier for the
    # episode's whole TTL once the symbol leaves the adjacency window.
    _seed_actor(fusion_db)
    upsert_earnings([{"symbol": "TSLA", "date": "2026-07-20"}], db_path=fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db, "TSLA")
    (dot,) = _dots(fusion_db, ep, "earnings_proximity")
    assert float(dot["points"]) == 1.0

    # Same open episode, 4 weekdays after the print — out of the drift window.
    later = "2026-07-24T14:30:00+00:00"
    insert_flow(_flow("flow-later", ts=later), fusion_db)
    _run_intake(fusion_db)
    (dot,) = _dots(fusion_db, ep, "earnings_proximity")
    assert float(dot["points"]) == 0.0
    # The zeroing branch is a raw UPDATE, outside the refresh_state upsert: it
    # has to clear the READING and advance the stamp too, or the dossier still
    # reads "0 weekdays out, as of the print" long after the window closed.
    assert dot["raw_value"] is None
    assert dot["ts"] == later
    detail = json.loads(dot["detail_json"])
    assert detail["side"] == "none"
    assert detail["as_of"] == "2026-07-24"
    assert "catalyst" not in _group_scores(fusion_db, ep)


def test_earnings_dot_refreshes_in_place(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    upsert_earnings([{"symbol": "TSLA", "date": "2026-07-20"}], db_path=fusion_db)
    _run_intake(fusion_db)
    insert_flow(_flow("flow-2"), fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db, "TSLA")
    assert len(_dots(fusion_db, ep, "earnings_proximity")) == 1


# ------------------------------------------------------ Round B: no per-name stamps


def test_regime_does_not_mint_per_name_dot(fusion_db: Path) -> None:
    """Thin regime_macro rows used to stamp every episode. Day-scoped context
    is regime_report on macro.db; fusion must not copy it onto TSLA."""
    _seed_actor(fusion_db)
    upsert_regime(
        [
            ("VIXCLS", "2026-07-16", 18.6),
            ("vix3m", "2026-07-16", 20.5),
            ("DGS2", "2026-07-16", 3.9),
            ("DGS10", "2026-07-16", 4.4),
        ],
        db_path=fusion_db,
    )
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db, "TSLA")
    assert _dots(fusion_db, ep, "regime") == []
    assert "context" not in _group_scores(fusion_db, ep)
    assert _meter(fusion_db, "regime_dots") == 0.0


def test_index_pressure_does_not_stamp_unrelated_names(fusion_db: Path) -> None:
    """The gauge is 13 underlyings. Stamping the market-wide net on TSLA was
    the 99.4% wrong-name bug. Dossier already filters to the symbol."""
    _seed_actor(fusion_db)
    upsert_minutes(
        [
            ("SPX", MARKET_DAY, 0, 1_000_000.0, 400_000.0, 100_000.0, 50, "market_wide"),
            ("NDX", MARKET_DAY, 1, 200_000.0, 500_000.0, 0.0, 20, "market_wide"),
        ],
        window="open",
        db_path=fusion_db,
    )
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db, "TSLA")
    assert _dots(fusion_db, ep, "index_pressure") == []
    assert "context" not in _group_scores(fusion_db, ep)
    assert _meter(fusion_db, "index_pressure_dots") == 0.0
