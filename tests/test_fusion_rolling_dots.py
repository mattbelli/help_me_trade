"""Rolling STATE dots: the shared refresh_state upsert + the attention peak fix.

A rolling dot is one row per (episode, component) that re-reads a live
measurement every pass. The ON CONFLICT used to rewrite only points/detail, so
raw_value and ts froze at the FIRST reading — a fabricated fact on the dossier
(earnings: "5 weekdays out" on the morning of the print) and, for attention,
a score pinned at the episode's all-time peak buzz for its whole 15-session
life. These pin the fix and the one-time zeroing migration.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from db.base import connect
from db.chart_features import init_chart_features
from db.earnings_calendar import upsert_earnings
from db.fusion import init_fusion_tables, put_atr
from db.options_flow import insert_event as insert_flow
from db.social_mentions import et_hour_bucket, record_mentions
from signals.fusion.intake import intake_once
from signals.fusion.scorer import score_decayed
from trading.market_hours import _ET

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


def _seed_actor(db: Path, event_id: str = "flow-1", *, ts: str = EVENT_TS) -> None:
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=db)
    insert_flow(_flow(event_id, ts=ts), db)


def _run_intake(db: Path) -> dict:
    return intake_once(db_path=db, price_lookup=lambda _s: Decimal("320.25"))


def _episode_id(db: Path, symbol: str = "TSLA") -> str:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT id FROM fusion_episodes WHERE symbol = ?", (symbol,)
        ).fetchone()
    assert row is not None
    return str(row["id"])


def _dots(db: Path, episode_id: str, component: str) -> list[dict]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM evidence_dots WHERE episode_id = ? AND component = ?",
            (episode_id, component),
        ).fetchall()
    return [dict(r) for r in rows]


def _seed_buzz(db: Path, *, today: int, baseline: int = 10, days: int = 3) -> None:
    """Make ``anomaly_ratio`` = today/baseline for reddit.

    get_source_buzz reads the CURRENT wall-clock ET hour bucket, so the samples
    have to hang off now() — the same hour on the prior ``days`` days is one
    sample each, giving a mean of exactly ``baseline``.
    """
    now_et = datetime.now(_ET)
    for back in range(1, days + 1):
        record_mentions(
            {"TSLA": {"reddit": baseline}},
            bucket=et_hour_bucket(now_et - timedelta(days=back)),
            db_path=db,
        )
    _set_today_buzz(db, today)


def _set_today_buzz(db: Path, mentions: int) -> None:
    bucket = et_hour_bucket()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO social_mentions(symbol, bucket, source, mentions)
            VALUES ('TSLA', ?, 'reddit', ?)
            ON CONFLICT(symbol, bucket, source) DO UPDATE SET
                mentions = excluded.mentions
            """,
            (bucket, mentions),
        )
        conn.commit()


# ----------------------------------------------------------------- attention


def test_attention_dot_is_rolling_and_tracks_the_reading_down(fusion_db: Path) -> None:
    _seed_buzz(fusion_db, today=40)  # ratio 4.0 → log2 = 2.0 points
    _seed_actor(fusion_db)
    _run_intake(fusion_db)

    ep = _episode_id(fusion_db)
    (dot,) = _dots(fusion_db, ep, "reddit_buzz")
    assert dot["source_event_id"] == f"{ep}:reddit_buzz"
    assert float(dot["points"]) == pytest.approx(2.0)
    assert float(dot["raw_value"]) == pytest.approx(4.0)

    # The buzz dies back below the spike gate: the SAME dot is zeroed — never
    # a second row, never the frozen peak.
    _set_today_buzz(fusion_db, 15)  # ratio 1.5, under FUSION_SOCIAL_SPIKE_RATIO
    later = "2026-07-17T15:30:00+00:00"
    insert_flow(_flow("flow-2", ts=later), fusion_db)
    _run_intake(fusion_db)

    (dot,) = _dots(fusion_db, ep, "reddit_buzz")
    assert float(dot["points"]) == 0.0
    assert float(dot["raw_value"]) == pytest.approx(1.5)
    assert dot["ts"] == later
    assert json.loads(dot["detail_json"])["state"] == "below_gate"


def test_attention_peak_does_not_pin_the_group_score(fusion_db: Path) -> None:
    _seed_buzz(fusion_db, today=80)  # ratio 8.0 → 3.0 points (the cap)
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    assert float(_dots(fusion_db, ep, "reddit_buzz")[0]["points"]) == pytest.approx(3.0)

    _set_today_buzz(fusion_db, 25)  # ratio 2.5 → log2 ≈ 1.32
    insert_flow(_flow("flow-2", ts="2026-07-17T15:30:00+00:00"), fusion_db)
    _run_intake(fusion_db)

    dots = _dots(fusion_db, ep, "reddit_buzz")
    assert len(dots) == 1
    assert float(dots[0]["points"]) == pytest.approx(1.3219, abs=1e-3)

    with connect(fusion_db) as conn:
        row = conn.execute(
            """
            SELECT score_json FROM fusion_score_history
            WHERE episode_id = ? ORDER BY id DESC LIMIT 1
            """,
            (ep,),
        ).fetchone()
    groups = json.loads(row["score_json"])["group_scores"]
    assert groups["attention"] == pytest.approx(1.3219, abs=1e-3)


def test_attention_never_moves_the_decayed_thesis(fusion_db: Path) -> None:
    """direction='none' — the attention change is a legacy-score move ONLY.
    The decayed thesis (the z gate's input) must be byte-identical whatever the
    attention dot's points are."""
    _seed_buzz(fusion_db, today=40)
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)

    from db.fusion import episode_dots

    now = "2026-07-17T15:00:00+00:00"
    dots = episode_dots(ep, db_path=fusion_db)
    assert any(d["component"] == "reddit_buzz" for d in dots)
    peaked = [
        {**d, "points": 3.0} if d["component"] == "reddit_buzz" else d for d in dots
    ]
    zeroed = [
        {**d, "points": 0.0} if d["component"] == "reddit_buzz" else d for d in dots
    ]
    assert json.dumps(score_decayed(peaked, now)["thesis"], sort_keys=True) == json.dumps(
        score_decayed(zeroed, now)["thesis"], sort_keys=True
    )


def test_legacy_bucket_keyed_attention_dots_are_zeroed_not_deleted(
    fusion_db: Path,
) -> None:
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    with connect(fusion_db) as conn:
        for dot_id, source_event_id, points in (
            ("legacy-1", "TSLA:2026-07-17T10:reddit", 3.0),
            ("rolling-1", f"{ep}:stocktwits_buzz", 2.0),
        ):
            conn.execute(
                """
                INSERT INTO evidence_dots(
                    id, episode_id, source_store, source_event_id, source_ingest_seq,
                    symbol, ts, component, actor_group, dot_kind, direction,
                    points, created_at
                ) VALUES (?, ?, 'social_mentions', ?, 0, 'TSLA', ?,
                          ?, 'attention', 'state', 'none', ?, ?)
                """,
                (
                    dot_id,
                    ep,
                    source_event_id,
                    EVENT_TS,
                    "reddit_buzz" if dot_id == "legacy-1" else "stocktwits_buzz",
                    points,
                    EVENT_TS,
                ),
            )
        # The migration already ran when this db was created — replay it.
        conn.execute("DELETE FROM fusion_migrations")
        conn.commit()

    init_fusion_tables(fusion_db)

    with connect(fusion_db) as conn:
        rows = {
            str(r["id"]): float(r["points"])
            for r in conn.execute(
                "SELECT id, points FROM evidence_dots WHERE actor_group = 'attention'"
            ).fetchall()
        }
    assert rows["legacy-1"] == 0.0  # zeroed, still present (history is not deleted)
    assert rows["rolling-1"] == 2.0  # a rolling dot is never touched

    # Guarded: a second init does not re-scan/re-zero anything new.
    with connect(fusion_db) as conn:
        conn.execute("UPDATE evidence_dots SET points = 3.0 WHERE id = 'legacy-1'")
        conn.commit()
    init_fusion_tables(fusion_db)
    with connect(fusion_db) as conn:
        assert (
            float(
                conn.execute(
                    "SELECT points FROM evidence_dots WHERE id = 'legacy-1'"
                ).fetchone()["points"]
            )
            == 3.0
        )


# ------------------------------------------------------------------ earnings


def test_earnings_weekday_count_and_ts_advance_across_days(fusion_db: Path) -> None:
    """The latent freeze: day-2's dot kept day-1's weekday count in raw_value
    and day-1's ts, so the dossier read '4 weekdays out' on the print's eve."""
    _seed_actor(fusion_db)
    upsert_earnings([{"symbol": "TSLA", "date": "2026-07-23"}], db_path=fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    (dot,) = _dots(fusion_db, ep, "earnings_proximity")
    assert float(dot["raw_value"]) == 4.0
    assert dot["ts"] == EVENT_TS

    later = "2026-07-22T14:30:00+00:00"  # the day before the print
    insert_flow(_flow("flow-later", ts=later), fusion_db)
    _run_intake(fusion_db)

    (dot,) = _dots(fusion_db, ep, "earnings_proximity")
    assert float(dot["raw_value"]) == 1.0
    assert dot["ts"] == later
    assert float(dot["points"]) == 1.0
