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
from db.earnings_calendar import upsert_earnings
from db.idea_dossiers import get_dossier
from db.social_mentions import record_mentions
from signals.dossier import (
    ANGLE_ORDER,
    _trading_days_between,
    build_and_store_dossier,
    build_dossier,
)

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
    assert counts["evidence"] == 2  # one claim per (component, group, direction)
    assert counts["flow"] == 2  # bull premium total + largest print
    assert counts["tape"] == 2  # bear notional total + largest print
    assert counts["structure"] == 1  # nearest session level (no active alerts)
    assert counts["history"] == 1

    structure = next(c for c in dossier["claims"] if c["angle"] == "structure")
    assert "PDH" in structure["statement"]  # nearest to anchor 245 is PDH @ 246
    history = next(c for c in dossier["claims"] if c["angle"] == "history")
    assert "1 won" in history["statement"] and "%" not in history["statement"]
    # evidence is LAST in ANGLE_ORDER — analytic angles precede it in the matrix.
    angles_in_order = []
    for claim in dossier["claims"]:
        if claim["angle"] not in angles_in_order:
            angles_in_order.append(claim["angle"])
    assert angles_in_order[-1] == "evidence"
    assert angles_in_order.index("flow") < angles_in_order.index("evidence")


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


def test_background_crowd_raw_value_renders_as_a_multiple(db: Path) -> None:
    # The evidence angle rendered raw_value for ACTOR groups only, so an rvol
    # dot came out as a bare "0 pts" claim with its reading dropped — and $2.35
    # would be a fabricated notional.
    episode = _seed_episode(db)
    _seed_dot(db, dot_id="d1", component="rvol_ex_prints", group="background_crowd",
              direction="none", points=0.0, raw_value="2.35", ts=_utc(18, 0))
    _seed_dot(db, dot_id="d2", component="option_sweep", group="options_actor",
              direction="bull", points=2.0, raw_value="300000", ts=_utc(18, 1))

    claims = build_dossier(episode, db_path=db, now=NOW)["claims"]
    rvol = next(c for c in claims if "rvol_ex_prints" in c["statement"])
    assert "2.35× RVOL-ex-prints" in rvol["statement"]
    assert "$" not in rvol["statement"]
    flow = next(c for c in claims if "option_sweep" in c["statement"])
    assert "$300,000 total" in flow["statement"]  # actor dollars unchanged


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
    # Unique (component, group, direction) keys so aggregation cannot collapse the
    # overflow — many identical dots would become one claim after D2.
    for k in range(250):
        _seed_dot(
            db,
            dot_id=f"d{k}",
            component=f"option_sweep_{k}",
            group="options_actor",
            direction="bull",
            points=2.5,
            raw_value="250000",
            ts=_utc(18, 0),
        )

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


def test_flow_primary_window_is_now_et_day_not_episode_birth(db: Path) -> None:
    """D1: market_day is opening-session-only; primary flow reads now's ET day."""
    birth = "2026-07-20"
    later = "2026-07-22"
    episode = _seed_episode(db, market_day=birth)
    _seed_flow(
        db,
        event_id="f_birth",
        right="C",
        direction="ask",
        premium=50_000,
        ts=f"{birth}T18:00:00+00:00",
    )
    _seed_flow(
        db,
        event_id="f_later",
        right="C",
        direction="ask",
        premium=400_000,
        ts=f"{later}T18:00:00+00:00",
    )
    now = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)

    dossier = build_dossier(episode, db_path=db, now=now)
    primary = [
        c
        for c in dossier["claims"]
        if c["angle"] == "flow" and f"[{later}]" in c["statement"]
    ]
    since_open = [
        c
        for c in dossier["claims"]
        if c["angle"] == "flow" and f"since episode opened {birth}" in c["statement"]
    ]
    assert primary, "primary window claims must name now's ET date"
    assert since_open, "multi-day episodes must emit a separate since-open aggregate"
    bull_primary = next(c for c in primary if c["direction"] == "bull" and "premium" in c["statement"])
    assert bull_primary["value"] == 400_000
    assert "50,000" not in bull_primary["statement"]  # birth-day alone is not the primary
    bull_since = next(
        c for c in since_open if c["direction"] == "bull" and "premium" in c["statement"]
    )
    assert bull_since["value"] == 450_000  # both days, distinct from primary


def test_tape_primary_window_is_now_et_day_not_episode_birth(db: Path) -> None:
    """D1: tape mirrors flow — primary window is now's ET day, not episode birth."""
    birth = "2026-07-20"
    later = "2026-07-22"
    episode = _seed_episode(db, market_day=birth)
    _seed_tape(
        db,
        event_id="t_birth",
        direction="buy",
        notional=50_000,
        ts=f"{birth}T18:00:00+00:00",
    )
    _seed_tape(
        db,
        event_id="t_later",
        direction="buy",
        notional=400_000,
        ts=f"{later}T18:00:00+00:00",
    )
    now = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)

    dossier = build_dossier(episode, db_path=db, now=now)
    primary = [
        c
        for c in dossier["claims"]
        if c["angle"] == "tape" and f"[{later}]" in c["statement"]
    ]
    since_open = [
        c
        for c in dossier["claims"]
        if c["angle"] == "tape" and f"since episode opened {birth}" in c["statement"]
    ]
    assert primary, "primary window claims must name now's ET date"
    assert since_open, "multi-day episodes must emit a separate since-open aggregate"
    bull_primary = next(
        c for c in primary if c["direction"] == "bull" and "notional" in c["statement"]
    )
    assert bull_primary["value"] == 400_000
    assert "50,000" not in bull_primary["statement"]
    bull_since = next(
        c for c in since_open if c["direction"] == "bull" and "notional" in c["statement"]
    )
    assert bull_since["value"] == 450_000


def test_since_open_emitted_only_for_multi_day_episode(db: Path) -> None:
    """D1 boundary: multi-day emits labelled since-open; single-day does not."""
    birth = "2026-07-20"
    later = "2026-07-22"
    episode = _seed_episode(db, market_day=birth)
    _seed_flow(
        db,
        event_id="f_birth",
        right="C",
        direction="ask",
        premium=50_000,
        ts=f"{birth}T18:00:00+00:00",
    )
    _seed_flow(
        db,
        event_id="f_later",
        right="C",
        direction="ask",
        premium=400_000,
        ts=f"{later}T18:00:00+00:00",
    )
    _seed_tape(
        db,
        event_id="t_birth",
        direction="sell",
        notional=80_000,
        ts=f"{birth}T18:00:00+00:00",
    )
    _seed_tape(
        db,
        event_id="t_later",
        direction="sell",
        notional=200_000,
        ts=f"{later}T18:00:00+00:00",
    )
    now = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)

    multi = build_dossier(episode, db_path=db, now=now)
    assert any(
        f"since episode opened {birth}" in c["statement"]
        for c in multi["claims"]
        if c["angle"] in ("flow", "tape")
    )

    single = build_dossier({**episode, "market_day": later}, db_path=db, now=now)
    assert not any(
        "since episode opened" in c["statement"]
        for c in single["claims"]
        if c["angle"] in ("flow", "tape")
    )


def test_catalyst_as_of_now_not_birth_day(db: Path) -> None:
    """D1: earnings trading-day count is as-of now, and the statement names that day."""
    birth = "2026-07-21"
    as_of = "2026-08-02"
    earn = "2026-08-26"
    episode = _seed_episode(db, market_day=birth)
    upsert_earnings(
        [{"symbol": SYMBOL, "date": earn, "hour": "amc", "source": "finnhub"}],
        db_path=db,
    )
    now = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)

    catalyst = next(
        c
        for c in build_dossier(episode, db_path=db, now=now)["claims"]
        if c["angle"] == "catalyst"
    )
    expected = _trading_days_between(as_of, earn)
    assert expected == 18  # measured live; pins the kill-switch
    assert catalyst["value"] == expected
    assert f"[as of {as_of}]" in catalyst["statement"]
    assert "26 trading day" not in catalyst["statement"]  # birth-day count must not appear


def test_now_selects_window_values_not_only_freshness(db: Path) -> None:
    """Intentional purity-contract change: now affects claim VALUES, not just fresh."""
    birth = "2026-07-20"
    episode = _seed_episode(db, market_day=birth)
    _seed_flow(
        db,
        event_id="f1",
        right="C",
        direction="ask",
        premium=100_000,
        ts=f"{birth}T18:00:00+00:00",
    )
    _seed_flow(
        db,
        event_id="f2",
        right="C",
        direction="ask",
        premium=250_000,
        ts="2026-07-22T18:00:00+00:00",
    )
    now_birth = datetime(2026, 7, 20, 18, 5, tzinfo=timezone.utc)
    now_later = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)

    early = build_dossier(episode, db_path=db, now=now_birth)
    late = build_dossier(episode, db_path=db, now=now_later)
    early_bull = next(
        c
        for c in early["claims"]
        if c["angle"] == "flow" and c["direction"] == "bull" and f"[{birth}]" in c["statement"]
    )
    late_bull = next(
        c
        for c in late["claims"]
        if c["angle"] == "flow"
        and c["direction"] == "bull"
        and "[2026-07-22]" in c["statement"]
    )
    assert early_bull["value"] == 100_000
    assert late_bull["value"] == 250_000
    assert early["claims"] != late["claims"]
    # Same now still deterministic.
    assert build_dossier(episode, db_path=db, now=now_later)["claims"] == late["claims"]


def test_evidence_aggregates_by_component_group_direction(db: Path) -> None:
    """D2: many dots of the same key collapse to one counted claim (news preserved)."""
    episode = _seed_episode(db)
    for k in range(5):
        _seed_dot(
            db,
            dot_id=f"sweep{k}",
            component="option_sweep",
            group="options_actor",
            direction="bull",
            points=2.0,
            raw_value="100000",
            ts=_utc(18, k),
        )
    for k in range(3):
        _seed_dot(
            db,
            dot_id=f"news{k}",
            component="news",
            group="catalyst",
            direction="none",
            points=0.0,
            raw_value=None,
            ts=_utc(17, k),
        )

    claims = [
        c
        for c in build_dossier(episode, db_path=db, now=NOW)["claims"]
        if c["angle"] == "evidence"
    ]
    assert len(claims) == 2  # not 8
    sweep = next(c for c in claims if "option_sweep" in c["statement"])
    news = next(c for c in claims if "news" in c["statement"])
    assert "5 dots" in sweep["statement"]
    assert "$500,000 total" in sweep["statement"]
    assert "3 dots" in news["statement"]
    assert sweep["value"] == 10.0  # sum of points


def test_summary_keeps_analysis_truncates_evidence_tail(db: Path) -> None:
    """D2: evidence last + aggregation → summary shows analysis, not only evidence."""
    episode = _seed_full(db)
    # direction=none so filler keys do not explode cross-angle contradiction
    # annotations (those alone can fill the 4k cap before structure/history).
    for k in range(200):
        _seed_dot(
            db,
            dot_id=f"extra{k}",
            component=f"news_item_{k}",
            group="catalyst",
            direction="none",
            points=0.0,
            raw_value=None,
            ts=_utc(18, 0),
        )

    summary = build_dossier(episode, db_path=db, now=NOW)["summary"]
    assert len(summary) <= 4000
    for angle in ("flow", "tape", "structure", "history"):
        assert f"[{angle}]" in summary, f"missing analytic angle {angle} in summary"
    # Evidence is last — if truncated, the marker follows evidence lines, not analysis.
    lines = [line for line in summary.splitlines() if not line.startswith("…")]
    analytic = [line for line in lines if not line.startswith("[evidence]")]
    evidence = [line for line in lines if line.startswith("[evidence]")]
    assert analytic, "analysis must survive the cap"
    if evidence and analytic:
        last_analytic = max(lines.index(line) for line in analytic)
        first_evidence = min(lines.index(line) for line in evidence)
        assert last_analytic < first_evidence
    assert summary.rstrip().endswith("claims truncated)") or "[evidence]" in summary


def test_intra_angle_sort_is_freshest_first(db: Path) -> None:
    """D2: claim_id order within an angle is freshest-ts first (cap keeps recent)."""
    episode = _seed_episode(db)
    _seed_dot(
        db,
        dot_id="old",
        component="option_sweep",
        group="options_actor",
        direction="bull",
        points=1.0,
        raw_value="100000",
        ts=_utc(16, 0),
    )
    _seed_dot(
        db,
        dot_id="new",
        component="dark_pool",
        group="equity_actor",
        direction="bear",
        points=2.0,
        raw_value="200000",
        ts=_utc(18, 0),
    )
    claims = [
        c
        for c in build_dossier(episode, db_path=db, now=NOW)["claims"]
        if c["angle"] == "evidence"
    ]
    assert claims[0]["claim_id"] == "evidence:0"
    assert "dark_pool" in claims[0]["statement"]  # fresher ts first
    assert "option_sweep" in claims[1]["statement"]
    # Determinism under the new sort: same inputs → same ids.
    again = [
        c
        for c in build_dossier(episode, db_path=db, now=NOW)["claims"]
        if c["angle"] == "evidence"
    ]
    assert [c["claim_id"] for c in again] == [c["claim_id"] for c in claims]