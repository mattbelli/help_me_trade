"""Fusion Phase C2 — deterministic dossier builder (signals/dossier.py)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from db import equity_tape as equity_tape_db
from db import fusion as fusion_db
from db import idea_outcomes as idea_outcomes_db
from db import options_flow as options_flow_db
from db.base import connect
from db.idea_dossiers import get_dossier
from db.social_mentions import record_mentions
from signals.dossier import ANGLE_ORDER, build_and_store_dossier, build_dossier

MARKET_DAY = "2026-07-20"
NOW = datetime(2026, 7, 20, 18, 5, tzinfo=timezone.utc)  # 14:05 ET, on the session
SYMBOL = "TSLA"
EP_ID = "ep_tsla_0720"


def _utc(hour_utc: int, minute: int = 0) -> str:
    """A UTC-ISO ts on MARKET_DAY (18:00 UTC == 14:00 ET during EDT)."""
    return f"{MARKET_DAY}T{hour_utc:02d}:{minute:02d}:00+00:00"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    fusion_db.init_fusion_tables(path)
    return path


def _seed_episode(
    db: Path,
    *,
    market_shock: int = 0,
    anchor_price: str | None = "245.00",
    ep_id: str = EP_ID,
    symbol: str = SYMBOL,
    market_day: str = MARKET_DAY,
) -> dict:
    ts = _utc(18)
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, anchor_price, market_shock
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ep_id, symbol, market_day, ts, ts, anchor_price, int(market_shock)),
        )
        conn.commit()
    return {
        "id": ep_id,
        "symbol": symbol,
        "market_day": market_day,
        "anchor_price": anchor_price,
        "market_shock": int(market_shock),
    }


def _seed_dot(
    db: Path,
    *,
    dot_id: str,
    component: str,
    group: str,
    direction: str,
    points: float,
    raw_value: str | None,
    ts: str,
    ep_id: str = EP_ID,
    symbol: str = SYMBOL,
) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO evidence_dots(
                id, episode_id, source_store, source_event_id, source_ingest_seq,
                symbol, ts, component, actor_group, dot_kind, direction,
                raw_value, points, detail_json, stamped, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dot_id, ep_id, "options_flow", dot_id, 0, symbol, ts, component,
                group, "event", direction, raw_value, float(points), "{}", 1, ts,
            ),
        )
        conn.commit()


def _seed_flow(db: Path, *, event_id: str, right: str, direction: str, premium: float, ts: str) -> None:
    options_flow_db.insert_event(
        {
            "id": event_id,
            "ts": ts,
            "underlying": SYMBOL,
            "occ_symbol": f"{SYMBOL}260717C00250000",
            "event_type": "sweep",
            "right": right,
            "direction": direction,
            "premium_usd": premium,
        },
        db_path=db,
    )


def _seed_tape(db: Path, *, event_id: str, direction: str, notional: float, ts: str, event_type: str = "dark_pool") -> None:
    equity_tape_db.insert_event(
        {
            "id": event_id,
            "ts": ts,
            "symbol": SYMBOL,
            "event_type": event_type,
            "direction": direction,
            "notional_usd": notional,
        },
        db_path=db,
    )


def _seed_history(db: Path, *, status: str = "won", symbol: str = SYMBOL) -> None:
    idea_id = idea_outcomes_db.register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol=symbol,
        direction="long",
        entry_zone={"low": "240", "high": "242"},
        stop="238",
        targets=["250"],
        as_of="2026-07-10T14:00:00+00:00",
        db_path=db,
    )
    assert idea_id is not None
    idea_outcomes_db.update_outcome(
        idea_id, status=status, resolved_at="2026-07-15T20:00:00+00:00", db_path=db
    )


def _seed_full(db: Path) -> dict:
    episode = _seed_episode(db)
    _seed_dot(db, dot_id="d1", component="option_sweep", group="options_actor",
              direction="bull", points=2.5, raw_value="250000", ts=_utc(18, 0))
    _seed_dot(db, dot_id="d2", component="dark_pool", group="equity_actor",
              direction="bear", points=1.5, raw_value="800000", ts=_utc(18, 5))
    _seed_flow(db, event_id="f1", right="C", direction="ask", premium=250000, ts=_utc(18, 0))
    _seed_flow(db, event_id="f2", right="C", direction="ask", premium=100000, ts=_utc(18, 2))
    _seed_tape(db, event_id="t1", direction="sell", notional=900000, ts=_utc(18, 5))
    fusion_db.put_session_levels(
        SYMBOL, MARKET_DAY, [{"name": "PDH", "price": 246.0}, {"name": "VAL", "price": 240.0}], db_path=db
    )
    _seed_history(db, status="won")
    return episode


def _angles(claims: list[dict]) -> Counter:
    return Counter(claim["angle"] for claim in claims)


def test_full_matrix_angles_and_counts(db: Path) -> None:
    episode = _seed_full(db)
    dossier = build_dossier(episode, db_path=db, now=NOW)

    counts = _angles(dossier["claims"])
    assert set(counts) == {"evidence", "flow", "tape", "structure", "history"}
    assert counts["evidence"] == 2  # one claim per dot
    assert counts["flow"] == 2  # bull premium total + largest print
    assert counts["tape"] == 2  # bear notional total + largest print
    assert counts["structure"] == 1  # nearest session level (no active alerts)
    assert counts["history"] == 1

    structure = next(c for c in dossier["claims"] if c["angle"] == "structure")
    assert "PDH" in structure["statement"]  # nearest to anchor 245 is PDH @ 246
    history = next(c for c in dossier["claims"] if c["angle"] == "history")
    assert "1 won" in history["statement"] and "%" not in history["statement"]


def test_claim_ids_deterministic(db: Path) -> None:
    episode = _seed_full(db)
    first = build_dossier(episode, db_path=db, now=NOW)
    second = build_dossier(episode, db_path=db, now=NOW)

    ids = [c["claim_id"] for c in first["claims"]]
    assert ids == [c["claim_id"] for c in second["claims"]]
    assert first["claims"] == second["claims"]  # values, ts, fresh, links all stable

    # Claims are grouped in ANGLE_ORDER; n runs 0..k-1 within each angle.
    per_angle: dict[str, list[int]] = {}
    seen_order: list[str] = []
    for cid in ids:
        angle, n = cid.split(":", 1)
        if angle not in seen_order:
            seen_order.append(angle)
        per_angle.setdefault(angle, []).append(int(n))
    assert seen_order == [a for a in ANGLE_ORDER if a in per_angle]
    for indices in per_angle.values():
        assert indices == list(range(len(indices)))


def test_warming_social_is_omitted(db: Path) -> None:
    episode = _seed_episode(db)
    _seed_dot(db, dot_id="d1", component="option_sweep", group="options_actor",
              direction="bull", points=2.0, raw_value="300000", ts=_utc(18, 0))
    # Today's mentions exist, but there is no multi-day baseline -> get_buzz warming.
    record_mentions({SYMBOL: {"reddit": 80}}, bucket=f"{MARKET_DAY}T14", db_path=db)

    dossier = build_dossier(episode, db_path=db, now=NOW)
    assert "attention" not in _angles(dossier["claims"])


def test_contradiction_links_are_reciprocal(db: Path) -> None:
    episode = _seed_episode(db)
    _seed_flow(db, event_id="f1", right="C", direction="ask", premium=250000, ts=_utc(18, 0))
    _seed_tape(db, event_id="t1", direction="sell", notional=900000, ts=_utc(18, 5))

    dossier = build_dossier(episode, db_path=db, now=NOW)
    flow_bull = next(c for c in dossier["claims"] if c["angle"] == "flow" and c["direction"] == "bull")
    tape_bear = next(c for c in dossier["claims"] if c["angle"] == "tape" and c["direction"] == "bear")

    assert tape_bear["claim_id"] in flow_bull["contradicts"]
    assert flow_bull["claim_id"] in tape_bear["contradicts"]


def test_summary_cap_respected_with_marker(db: Path) -> None:
    episode = _seed_episode(db)
    for k in range(250):
        _seed_dot(db, dot_id=f"d{k}", component="option_sweep", group="options_actor",
                  direction="bull", points=2.5, raw_value="250000", ts=_utc(18, 0))

    dossier = build_dossier(episode, db_path=db, now=NOW)
    summary = dossier["summary"]
    assert len(summary) <= 4000
    assert summary.rstrip().endswith("claims truncated)")
    # Truncation drops WHOLE claims — every non-marker line is a complete "[angle] ..." row.
    for line in summary.splitlines():
        assert line.startswith("[evidence]") or line.startswith("… (+")


def test_empty_store_yields_evidence_only_dossier(db: Path) -> None:
    episode = _seed_episode(db, anchor_price=None)
    _seed_dot(db, dot_id="d1", component="option_sweep", group="options_actor",
              direction="bull", points=3.0, raw_value="500000", ts=_utc(18, 0))

    dossier = build_dossier(episode, db_path=db, now=NOW)
    assert set(_angles(dossier["claims"])) == {"evidence"}
    assert len(dossier["claims"]) == 1
    assert dossier["summary"].startswith("[evidence]")


def test_get_put_roundtrip(db: Path) -> None:
    episode = _seed_full(db)
    dossier = build_and_store_dossier(episode, db_path=db, now=NOW)

    stored = get_dossier(EP_ID, db_path=db)
    assert stored is not None
    assert stored["symbol"] == SYMBOL
    assert stored["market_day"] == MARKET_DAY
    assert stored["claims"] == dossier["claims"]
    assert stored["claim_count"] == len(dossier["claims"])
    expected_contradictions = sum(len(c["contradicts"]) for c in dossier["claims"]) // 2
    assert stored["contradiction_count"] == expected_contradictions

    # A re-investigation rebuilds in place — still one row for the episode.
    build_and_store_dossier(episode, db_path=db, now=NOW)
    with connect(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM idea_dossiers WHERE episode_id = ?", (EP_ID,)
        ).fetchone()[0]
    assert n == 1
