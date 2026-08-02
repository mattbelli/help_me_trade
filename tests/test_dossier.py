"""Fusion Phase C2 — deterministic dossier builder (signals/dossier.py)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from db import equity_tape as equity_tape_db
from db import fusion as fusion_db
from db import idea_outcomes as idea_outcomes_db
from db import news as news_db
from db import options_flow as options_flow_db
from db.base import connect
from db.earnings_calendar import upsert_earnings
from db.idea_dossiers import get_dossier
from db.macro import upsert_macro
from db.social_mentions import record_mentions
from signals.dossier import (
    ANGLE_ORDER,
    _trading_days_between,
    build_and_store_dossier,
    build_dossier,
)
from signals.macro_substrate import REPORT_AXES

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


def _seed_news(
    db: Path,
    *,
    news_id: str,
    headline: str,
    published_at: str,
    symbols: list[str],
) -> None:
    news_db.init_db(db)
    news_db.upsert_news_item(
        {
            "id": news_id,
            "headline": headline,
            "summary": "",
            "url": "",
            "source": "test",
            "author": "",
            "symbols": [s.upper() for s in symbols],
            "image_url": None,
            "published_at": published_at,
            "updated_at": None,
        },
        db_path=db,
    )


def test_news_primary_window_is_now_et_day_not_episode_birth(db: Path) -> None:
    """D3/D1 guard: news primary window is now's ET day, not birth market_day."""
    birth = "2026-07-20"
    later = "2026-07-22"
    episode = _seed_episode(db, market_day=birth)
    # Two on birth, one on later — primary value must be 1 (later), not 2 (birth).
    _seed_news(
        db,
        news_id="n_birth_a",
        headline="Birth-day headline A",
        published_at=f"{birth}T18:00:00Z",
        symbols=[SYMBOL],
    )
    _seed_news(
        db,
        news_id="n_birth_b",
        headline="Birth-day headline B",
        published_at=f"{birth}T19:00:00Z",
        symbols=[SYMBOL],
    )
    _seed_news(
        db,
        news_id="n_later",
        headline="Later-day headline",
        published_at=f"{later}T18:00:00.331Z",
        symbols=[SYMBOL],
    )
    now = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)

    dossier = build_dossier(episode, db_path=db, now=now)
    news_claims = [c for c in dossier["claims"] if c["angle"] == "news"]
    primary = [
        c
        for c in news_claims
        if "news item(s)" in c["statement"] and f"[{later}]" in c["statement"]
    ]
    since_open = [
        c
        for c in news_claims
        if "news item(s)" in c["statement"]
        and f"since episode opened {birth}" in c["statement"]
    ]
    assert primary, "primary window claims must name now's ET date"
    assert since_open, "multi-day episodes must emit a separate since-open aggregate"
    assert primary[0]["value"] == 1  # later day only — not the 2 birth-day rows
    assert since_open[0]["value"] == 3
    # Headlines are freshest-first across the join; later-day must appear.
    assert any("Later-day headline" in c["statement"] for c in news_claims)


def test_news_headline_cap_preserves_total_count(db: Path) -> None:
    """D3: 3 freshest headlines + remainder row; total never silently deleted."""
    episode = _seed_episode(db)
    for k in range(5):
        _seed_news(
            db,
            news_id=f"n{k}",
            headline=f"Headline number {k}",
            published_at=f"{MARKET_DAY}T18:{k:02d}:00Z",
            symbols=[SYMBOL, "SPY"] if k == 4 else [SYMBOL],
        )
    dossier = build_dossier(episode, db_path=db, now=NOW)
    news_claims = [c for c in dossier["claims"] if c["angle"] == "news"]
    headlines = [c for c in news_claims if c["statement"].startswith('"')]
    remainder = [c for c in news_claims if c["statement"].startswith("+")]
    assert len(headlines) == 3
    assert len(remainder) == 1
    assert "5 total" in remainder[0]["statement"]
    assert f"[{MARKET_DAY}]" in remainder[0]["statement"]  # remainder names its window
    assert remainder[0]["value"] == 5
    # Freshest three: k=4,3,2 — tag breadth rides the claim.
    assert "2 symbol(s) tagged" in headlines[0]["statement"] or any(
        "2 symbol(s) tagged" in c["statement"] for c in headlines
    )
    assert all(c["direction"] == "none" for c in news_claims)


def test_news_scoped_to_episode_window_not_all_time(db: Path) -> None:
    """D3 defect fix: headlines/total/remainder are episode-windowed, not all-time."""
    birth = "2026-07-20"
    later = "2026-07-22"
    pre = "2026-05-07"
    episode = _seed_episode(db, market_day=birth)
    _seed_news(
        db,
        news_id="n_pre",
        headline="Months-old pre-episode headline",
        published_at=f"{pre}T18:00:00Z",
        symbols=[SYMBOL],
    )
    for k in range(4):
        _seed_news(
            db,
            news_id=f"n_win{k}",
            headline=f"In-window headline {k}",
            published_at=f"{later}T18:{k:02d}:00Z",
            symbols=[SYMBOL],
        )
    now = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)

    news_claims = [
        c
        for c in build_dossier(episode, db_path=db, now=now)["claims"]
        if c["angle"] == "news"
    ]
    assert not any("Months-old" in c["statement"] for c in news_claims)
    since_open = next(
        c
        for c in news_claims
        if "news item(s)" in c["statement"]
        and f"since episode opened {birth}" in c["statement"]
    )
    remainder = next(c for c in news_claims if c["statement"].startswith("+"))
    assert since_open["value"] == 4
    assert remainder["value"] == 4  # not 5 (all-time)
    assert "4 total" in remainder["statement"]
    assert f"since episode opened {birth}" in remainder["statement"]
    assert "+1 more" in remainder["statement"]


def test_news_omitted_when_only_outside_episode_window(db: Path) -> None:
    """D3: pre-open news alone must not surface months-old headlines."""
    birth = "2026-07-20"
    episode = _seed_episode(db, market_day=birth)
    _seed_news(
        db,
        news_id="n_pre",
        headline="Ancient headline only",
        published_at="2026-05-07T18:00:00Z",
        symbols=[SYMBOL],
    )
    now = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)
    dossier = build_dossier(episode, db_path=db, now=now)
    assert "news" not in _angles(dossier["claims"])


def test_news_headlines_name_et_publish_date(db: Path) -> None:
    """D3: each headline statement carries its ET date (_render_summary is statement-only)."""
    birth = "2026-07-20"
    day_a = "2026-07-20"
    day_b = "2026-07-22"
    episode = _seed_episode(db, market_day=birth)
    _seed_news(
        db,
        news_id="n_a",
        headline="Older in-window",
        published_at=f"{day_a}T18:00:00Z",
        symbols=[SYMBOL],
    )
    _seed_news(
        db,
        news_id="n_b",
        headline="Newer in-window",
        published_at=f"{day_b}T18:00:00.331Z",
        symbols=[SYMBOL],
    )
    now = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)
    headlines = [
        c
        for c in build_dossier(episode, db_path=db, now=now)["claims"]
        if c["angle"] == "news" and c["statement"].startswith('"')
    ]
    assert len(headlines) == 2
    assert any(f'"Newer in-window" [{day_b}]' in c["statement"] for c in headlines)
    assert any(f'"Older in-window" [{day_a}]' in c["statement"] for c in headlines)


def test_news_angle_omitted_when_symbol_has_no_news(db: Path) -> None:
    episode = _seed_episode(db)
    _seed_news(
        db,
        news_id="other",
        headline="About AAPL only",
        published_at=f"{MARKET_DAY}T18:00:00Z",
        symbols=["AAPL"],
    )
    dossier = build_dossier(episode, db_path=db, now=NOW)
    assert "news" not in _angles(dossier["claims"])


def test_news_direction_is_always_none(db: Path) -> None:
    episode = _seed_episode(db)
    _seed_news(
        db,
        news_id="n1",
        headline="Bullish surge sends shares soaring",
        published_at=f"{MARKET_DAY}T18:00:00Z",
        symbols=[SYMBOL],
    )
    news_claims = [
        c for c in build_dossier(episode, db_path=db, now=NOW)["claims"] if c["angle"] == "news"
    ]
    assert news_claims
    assert all(c["direction"] == "none" for c in news_claims)


def test_news_claim_ids_deterministic(db: Path) -> None:
    episode = _seed_episode(db)
    _seed_news(
        db,
        news_id="n1",
        headline="First &#39;quoted&#39; headline",
        published_at=f"{MARKET_DAY}T18:00:00.331Z",
        symbols=[SYMBOL],
    )
    _seed_news(
        db,
        news_id="n2",
        headline="Second headline",
        published_at=f"{MARKET_DAY}T17:00:00Z",
        symbols=[SYMBOL, "MSFT", "AMZN"],
    )
    first = build_dossier(episode, db_path=db, now=NOW)
    second = build_dossier(episode, db_path=db, now=NOW)
    news_first = [c for c in first["claims"] if c["angle"] == "news"]
    news_second = [c for c in second["claims"] if c["angle"] == "news"]
    assert news_first
    assert [c["claim_id"] for c in news_first] == [c["claim_id"] for c in news_second]
    assert news_first == news_second
    # HTML entities decoded, not paraphrased; tag breadth + ET date surfaced.
    assert any("'quoted'" in c["statement"] for c in news_first)
    assert any("3 symbol(s) tagged" in c["statement"] for c in news_first)
    assert any(f"[{MARKET_DAY}]" in c["statement"] and c["statement"].startswith('"')
               for c in news_first)
    # news sits next to catalyst in ANGLE_ORDER; evidence still last.
    assert ANGLE_ORDER.index("news") == ANGLE_ORDER.index("catalyst") + 1
    assert ANGLE_ORDER[-1] == "evidence"


def test_news_reserved_slot_surfaces_narrow_breadth(db: Path) -> None:
    """Founder: slot A = min tag breadth so freshest-first cannot bury company news."""
    episode = _seed_episode(db)
    # Three wide freshest roundups would fill a pure freshest-first top-3.
    for k, breadth_syms in enumerate(
        [
            [SYMBOL, "A", "B", "C", "D", "E", "F", "G", "H"],  # 9
            [SYMBOL, "A", "B", "C", "D", "E", "F", "G", "H"],  # 9
            [SYMBOL] + [f"S{i}" for i in range(15)],  # 16
        ]
    ):
        _seed_news(
            db,
            news_id=f"wide{k}",
            headline=f"Wide roundup {k}",
            published_at=f"{MARKET_DAY}T18:{50 - k:02d}:00Z",
            symbols=breadth_syms,
        )
    # Older, narrow — pure freshest-first would drop this.
    _seed_news(
        db,
        news_id="narrow",
        headline="Company-only filing note",
        published_at=f"{MARKET_DAY}T12:00:00Z",
        symbols=[SYMBOL],
    )
    headlines = [
        c
        for c in build_dossier(episode, db_path=db, now=NOW)["claims"]
        if c["angle"] == "news" and c["statement"].startswith('"')
    ]
    assert len(headlines) == 3
    assert any("Company-only filing note" in c["statement"] for c in headlines)
    assert any("1 symbol(s) tagged" in c["statement"] for c in headlines)
    # No editorial label — breadth alone discriminates.
    assert not any("company-specific" in c["statement"].lower() for c in headlines)


def test_news_reserved_slot_no_duplicate_when_narrow_is_freshest(db: Path) -> None:
    """When the min-breadth story is also the freshest, it appears once, not twice."""
    episode = _seed_episode(db)
    _seed_news(
        db,
        news_id="narrow_fresh",
        headline="Fresh narrow",
        published_at=f"{MARKET_DAY}T19:00:00Z",
        symbols=[SYMBOL],
    )
    _seed_news(
        db,
        news_id="wide_old",
        headline="Older wide",
        published_at=f"{MARKET_DAY}T18:00:00Z",
        symbols=[SYMBOL, "MSFT", "AMZN", "META"],
    )
    _seed_news(
        db,
        news_id="wide_older",
        headline="Oldest wide",
        published_at=f"{MARKET_DAY}T17:00:00Z",
        symbols=[SYMBOL, "MSFT", "AMZN", "META"],
    )
    headlines = [
        c
        for c in build_dossier(episode, db_path=db, now=NOW)["claims"]
        if c["angle"] == "news" and c["statement"].startswith('"')
    ]
    assert len(headlines) == 3
    assert sum(1 for c in headlines if "Fresh narrow" in c["statement"]) == 1
    statements = [c["statement"] for c in headlines]
    assert len(statements) == len(set(statements))


def test_news_reserved_slot_remainder_reconciles(db: Path) -> None:
    """Remainder is still window_total - len(shown) under the reserved-slot policy."""
    episode = _seed_episode(db)
    for k in range(6):
        symbols = [SYMBOL] if k == 5 else [SYMBOL, "SPY", "QQQ", "IWM"]
        _seed_news(
            db,
            news_id=f"n{k}",
            headline=f"Story {k}",
            published_at=f"{MARKET_DAY}T18:{k:02d}:00Z",
            symbols=symbols,
        )
    news_claims = [
        c for c in build_dossier(episode, db_path=db, now=NOW)["claims"] if c["angle"] == "news"
    ]
    headlines = [c for c in news_claims if c["statement"].startswith('"')]
    remainder = next(c for c in news_claims if c["statement"].startswith("+"))
    assert len(headlines) == 3
    assert remainder["value"] == 6
    assert "+3 more" in remainder["statement"]
    assert "6 total" in remainder["statement"]
    assert any("Story 5" in c["statement"] and "1 symbol(s) tagged" in c["statement"]
               for c in headlines)


def test_news_reserved_slot_deterministic(db: Path) -> None:
    """Same pool → identical claim_ids under min-breadth + freshest remaining."""
    episode = _seed_episode(db)
    _seed_news(
        db,
        news_id="a",
        headline="Narrow A",
        published_at=f"{MARKET_DAY}T14:00:00Z",
        symbols=[SYMBOL],
    )
    _seed_news(
        db,
        news_id="b",
        headline="Narrow B same breadth older",
        published_at=f"{MARKET_DAY}T13:00:00Z",
        symbols=[SYMBOL],
    )
    _seed_news(
        db,
        news_id="w1",
        headline="Wide 1",
        published_at=f"{MARKET_DAY}T18:00:00Z",
        symbols=[SYMBOL, "X", "Y", "Z"],
    )
    _seed_news(
        db,
        news_id="w2",
        headline="Wide 2",
        published_at=f"{MARKET_DAY}T17:00:00Z",
        symbols=[SYMBOL, "X", "Y", "Z"],
    )
    first = build_dossier(episode, db_path=db, now=NOW)
    second = build_dossier(episode, db_path=db, now=NOW)
    assert first["claims"] == second["claims"]
    headlines = [c for c in first["claims"] if c["angle"] == "news" and c["statement"].startswith('"')]
    # Min breadth tie → freshest narrow (A), not older B; B is not among the 3.
    assert any("Narrow A" in c["statement"] for c in headlines)
    assert not any("Narrow B" in c["statement"] for c in headlines)


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


# --------------------------------------------------------------------------- D4 macro substrate


def _seed_macro_axes(
    macro_db: Path,
    *,
    session: str = "2026-07-16",
    dollar_last: str | None = None,
) -> None:
    """Seed history for all four REPORT_AXES.

    ``dollar_last`` caps the dollar series (inclusive) so as-of ``session`` can
    resolve to an earlier ``date_used`` (FRED lag). ``None`` → dollar tracks
    the other axes through ``session``.
    """
    rows: list[tuple[str, str, float]] = []
    for i in range(1, 11):
        d = f"2026-07-{i + 6:02d}"  # 07-07 .. 07-16
        rows.append(("VIXCLS", d, 15.0 + i))
        rows.append(("BAMLH0A0HYM2", d, 3.0 + 0.1 * i))
        rows.append(("DGS2", d, 4.0))
        rows.append(("DGS10", d, 4.0 + 0.05 * i))
        if dollar_last is None or d <= dollar_last:
            rows.append(("DTWEXBGS", d, 120.0 - i))
    upsert_macro(rows, db_path=macro_db)


def _macro_context_claims(dossier: dict) -> list[dict]:
    return [
        c
        for c in dossier["claims"]
        if c["angle"] == "context" and c["statement"].startswith("macro ")
    ]


def test_macro_axes_reach_context_with_date_used_and_depth(
    db: Path, tmp_path: Path
) -> None:
    """D4: four substrate axes land on context; date_used named; depth surfaced."""
    macro_db = tmp_path / "macro.db"
    _seed_macro_axes(macro_db, session="2026-07-16", dollar_last="2026-07-10")
    episode = _seed_episode(db)
    now = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    dossier = build_dossier(episode, db_path=db, macro_db_path=macro_db, now=now)
    macros = _macro_context_claims(dossier)
    assert len(macros) == len(REPORT_AXES)
    by_prefix = {c["statement"].split(":")[0]: c for c in macros}
    assert set(by_prefix) == {
        f"macro {axis} {series}" for axis, series in REPORT_AXES.items()
    }
    # Vol observes on session day; dollar lags to 07-10 (≠ window_day).
    assert "[as of 2026-07-16]" in by_prefix["macro vol VIXCLS"]["statement"]
    dollar_stmt = by_prefix["macro dollar DTWEXBGS"]["statement"]
    assert "[as of 2026-07-10]" in dollar_stmt
    assert dollar_stmt.count("2026-07-16") == 0  # window_day must not appear
    # Values + depth come from the store (not constants); n=10 from seed for vol.
    assert by_prefix["macro vol VIXCLS"]["value"] == 25.0
    assert "n=10" in by_prefix["macro vol VIXCLS"]["statement"]
    assert "pctile" in by_prefix["macro credit BAMLH0A0HYM2"]["statement"]
    assert all(c["direction"] == "none" for c in macros)
    # Old hand-rolled regime line must not survive alongside substrate axes.
    assert not any(
        c["statement"].startswith("regime:")
        for c in dossier["claims"]
        if c["angle"] == "context"
    )


def test_macro_stale_axis_reported_not_dropped(db: Path, tmp_path: Path) -> None:
    """D4: an axis past REPORT_STALE_DAYS is stated missing, never omitted."""
    macro_db = tmp_path / "macro.db"
    # Vol fresh through 07-16; dollar last seen 07-01 → 15 days before → missing.
    rows: list[tuple[str, str, float]] = []
    for i in range(1, 11):
        d = f"2026-07-{i + 6:02d}"
        rows.append(("VIXCLS", d, 15.0 + i))
        rows.append(("BAMLH0A0HYM2", d, 3.0 + 0.1 * i))
        rows.append(("DGS2", d, 4.0))
        rows.append(("DGS10", d, 4.0 + 0.05 * i))
    rows.append(("DTWEXBGS", "2026-07-01", 120.0))
    upsert_macro(rows, db_path=macro_db)
    episode = _seed_episode(db)
    now = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    macros = _macro_context_claims(
        build_dossier(episode, db_path=db, macro_db_path=macro_db, now=now)
    )
    dollar = next(c for c in macros if "dollar" in c["statement"])
    assert "missing" in dollar["statement"]
    assert "[as of 2026-07-16]" in dollar["statement"]
    assert dollar["value"] is None
    assert len(macros) == 4  # stale axis kept, not dropped


def test_macro_absent_db_degrades_without_raising(db: Path, tmp_path: Path) -> None:
    """D4: missing macro.db → no macro claims, no raise, AND path stays absent.

    Pins the ``is_file()`` guard: ``regime_report`` → ``series_history`` →
    ``init_macro_table`` CREATES an empty sqlite file on a nonexistent path, so
    without the guard this read-only module would write stub DBs.
    """
    episode = _seed_episode(db)
    absent = tmp_path / "no_such_macro.db"
    assert not absent.exists()
    dossier = build_dossier(episode, db_path=db, macro_db_path=absent, now=NOW)
    assert _macro_context_claims(dossier) == []
    assert not absent.exists(), "read path must not create a stub macro.db"


def test_macro_fresh_flag_tracks_date_used(db: Path, tmp_path: Path) -> None:
    """D4 review: ts is ET-noon on date_used; missing never fresh.

    Publication-lag freshness (prior-day vs week-old) is covered by
    ``test_macro_publication_lag_freshness_contrast`` — this one pins ts shape
    + the missing-axis path + the UTC-midnight trap on ``_et_noon_iso``.
    """
    episode = _seed_episode(db)
    now = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)  # 14:00 ET on 07-16

    macro_db = tmp_path / "macro.db"
    _seed_macro_axes(macro_db, session="2026-07-16", dollar_last="2026-07-10")
    macros = {
        c["statement"].split(":")[0]: c
        for c in _macro_context_claims(
            build_dossier(episode, db_path=db, macro_db_path=macro_db, now=now)
        )
    }
    # ET-noon on date_used (not UTC midnight, not state_ts).
    assert macros["macro vol VIXCLS"]["ts"] == "2026-07-16T12:00:00-04:00"
    assert macros["macro dollar DTWEXBGS"]["ts"] == "2026-07-10T12:00:00-04:00"
    assert "[as of 2026-07-10]" in macros["macro dollar DTWEXBGS"]["statement"]

    stale_db = tmp_path / "macro_stale.db"
    rows: list[tuple[str, str, float]] = []
    for i in range(1, 11):
        d = f"2026-07-{i + 6:02d}"
        rows.append(("VIXCLS", d, 15.0 + i))
        rows.append(("BAMLH0A0HYM2", d, 3.0 + 0.1 * i))
        rows.append(("DGS2", d, 4.0))
        rows.append(("DGS10", d, 4.0 + 0.05 * i))
    rows.append(("DTWEXBGS", "2026-07-01", 120.0))
    upsert_macro(rows, db_path=stale_db)
    missing = next(
        c
        for c in _macro_context_claims(
            build_dossier(episode, db_path=db, macro_db_path=stale_db, now=now)
        )
        if "dollar" in c["statement"] and "missing" in c["statement"]
    )
    assert missing["fresh"] is False
    assert missing["ts"] == ""


def test_macro_publication_lag_freshness_contrast(db: Path, tmp_path: Path) -> None:
    """D4 fix2: prior-day FRED prints are fresh; a week-old axis alone is stale.

    Mirrors real cadence (FRED never publishes same-day mid-session). Assert BOTH
    sides in one dossier — the point is the contrast the all-stale render erased.
    """
    macro_db = tmp_path / "macro.db"
    # History through 07-30 for vol/credit/curve; dollar stops at 07-24 (7-day gap).
    rows: list[tuple[str, str, float]] = []
    for i in range(1, 11):
        d = f"2026-07-{i + 20:02d}"  # 07-21 .. 07-30
        rows.append(("VIXCLS", d, 15.0 + i))
        rows.append(("BAMLH0A0HYM2", d, 3.0 + 0.1 * i))
        rows.append(("DGS2", d, 4.0))
        rows.append(("DGS10", d, 4.0 + 0.05 * i))
        if d <= "2026-07-24":
            rows.append(("DTWEXBGS", d, 120.0 - i))
    upsert_macro(rows, db_path=macro_db)

    episode = _seed_episode(db)
    # Mid-session Friday 07-31 — prior calendar day is 07-30 (real FRED lag).
    now = datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc)  # 14:00 ET
    dossier = build_dossier(episode, db_path=db, macro_db_path=macro_db, now=now)
    macros = {
        c["statement"].split(":")[0]: c for c in _macro_context_claims(dossier)
    }
    assert macros["macro vol VIXCLS"]["fresh"] is True
    assert macros["macro credit BAMLH0A0HYM2"]["fresh"] is True
    assert macros["macro curve DGS10-DGS2"]["fresh"] is True
    assert macros["macro dollar DTWEXBGS"]["fresh"] is False
    assert "[as of 2026-07-30]" in macros["macro vol VIXCLS"]["statement"]
    assert "[as of 2026-07-24]" in macros["macro dollar DTWEXBGS"]["statement"]

    # Summary: week-old dollar alone carries [stale]; prior-day axes do not.
    summary = dossier["summary"]
    dollar_line = next(
        line for line in summary.splitlines() if "macro dollar" in line
    )
    vol_line = next(line for line in summary.splitlines() if "macro vol" in line)
    assert "[stale]" in dollar_line
    assert "[stale]" not in vol_line
    assert "[stale]" not in next(
        line for line in summary.splitlines() if "macro credit" in line
    )


def test_macro_weekend_gap_still_fresh(db: Path, tmp_path: Path) -> None:
    """D4 fix2: Fri observation → Mon/Tue build stays fresh (weekend ± holiday)."""
    macro_db = tmp_path / "macro.db"
    rows: list[tuple[str, str, float]] = []
    for i in range(1, 11):
        d = f"2026-07-{i + 14:02d}"  # 07-15 .. 07-24 (Fri)
        rows.append(("VIXCLS", d, 15.0 + i))
        rows.append(("BAMLH0A0HYM2", d, 3.0 + 0.1 * i))
        rows.append(("DGS2", d, 4.0))
        rows.append(("DGS10", d, 4.0 + 0.05 * i))
        rows.append(("DTWEXBGS", d, 120.0 - i))
    upsert_macro(rows, db_path=macro_db)
    episode = _seed_episode(db)

    # Monday after Friday obs (gap=3).
    mon = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
    mon_macros = _macro_context_claims(
        build_dossier(episode, db_path=db, macro_db_path=macro_db, now=mon)
    )
    assert mon_macros and all(c["fresh"] for c in mon_macros)
    assert all("[as of 2026-07-24]" in c["statement"] for c in mon_macros)

    # Tuesday after Friday obs (gap=4 — weekend + one market holiday).
    tue = datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc)
    tue_macros = _macro_context_claims(
        build_dossier(episode, db_path=db, macro_db_path=macro_db, now=tue)
    )
    assert tue_macros and all(c["fresh"] for c in tue_macros)


def test_proto_without_fresh_still_uses_is_fresh(db: Path) -> None:
    """D4 fix2: protos that omit ``fresh`` still route through ``_is_fresh``."""
    episode = _seed_episode(db, market_shock=1)
    # Prior-day news: session angle, no explicit fresh → must be stale via _is_fresh.
    _seed_news(
        db,
        news_id="n_old",
        headline="Yesterday's print",
        published_at="2026-07-30T18:00:00Z",
        symbols=[SYMBOL],
    )
    now = datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc)
    dossier = build_dossier(
        episode, db_path=db, macro_db_path=db.parent / "absent.db", now=now
    )
    news = [
        c
        for c in dossier["claims"]
        if c["angle"] == "news" and c["statement"].startswith('"')
    ]
    assert news and all(c["fresh"] is False for c in news)
    shock = [
        c
        for c in dossier["claims"]
        if c["angle"] == "context" and "market-shock" in c["statement"]
    ]
    # market-shock uses state_ts (= now) and does not declare fresh → _is_fresh True.
    assert shock and shock[0]["fresh"] is True


def test_macro_claims_deterministic(db: Path, tmp_path: Path) -> None:
    """D4: same episode + same now → identical macro claim_ids / statements."""
    macro_db = tmp_path / "macro.db"
    _seed_macro_axes(macro_db)
    episode = _seed_episode(db)
    now = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    first = build_dossier(episode, db_path=db, macro_db_path=macro_db, now=now)
    second = build_dossier(episode, db_path=db, macro_db_path=macro_db, now=now)
    assert first["claims"] == second["claims"]
    macros = _macro_context_claims(first)
    assert [c["claim_id"] for c in macros] == [
        c["claim_id"] for c in _macro_context_claims(second)
    ]
    assert all(c["claim_id"].startswith("context:") for c in macros)