"""Fusion Phase C2 — deterministic dossier builder (signals/dossier.py)."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai.perception.resources import query as perception_query
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
from research.edgar_bulk import ensure_bulk_schema
from signals import dossier as dossier_mod
from signals.dossier import (
    ANGLE_ORDER,
    EDGAR_CHECK_MAX_AGE_DAYS,
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


@pytest.fixture(autouse=True)
def _no_ambient_edgar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The EDGAR and macro path defaults point at the checkout's real ``data/``.

    A test that does not seed its own corpus must read nothing, and no test may
    behave differently depending on whether this machine happens to hold the
    16 GB corpus. Tests that DO exercise the angle pass their fixture paths in
    explicitly, which overrides this.

    ``macro_db_path`` belongs here too: D4 wired the macro substrate into the
    context angle behind the same kwdefault, so on a box holding ``data/macro.db``
    an unseeded episode grew a macro claim and the angle assertions failed.
    """
    absent = tmp_path / "no_such_data"
    for fn in (build_dossier, build_and_store_dossier):
        monkeypatch.setitem(fn.__kwdefaults__, "facts_db_path", absent / "facts.db")
        monkeypatch.setitem(
            fn.__kwdefaults__, "fundamentals_db_path", absent / "fundamentals.db"
        )
        monkeypatch.setitem(fn.__kwdefaults__, "macro_db_path", absent / "macro.db")


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
    """Seed history for the four price/credit REPORT_AXES.

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
    # One claim per axis — the invariant, not a count. A literal here breaks
    # every time REPORT_AXES grows, which says nothing about D4.
    assert len(macros) == len(REPORT_AXES)  # stale axis kept, not dropped


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
        # Every REPORT_AXES input, or the assertion below degrades into "the
        # seeded axes are fresh" and stops testing the weekend gap at all.
        rows.append(("VXVCLS", d, 18.0 + i))  # vol_term
        rows.append(("SOFR", d, 3.6))  # funding
        rows.append(("IORB", d, 3.65))
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

# ------------------------------------------------------- D5: fundamentals angle

# Mirrors the measured shape of a real 10-Q (NVDA's Q1 FY2027, filed 2026-05-20):
# the fiscal quarter end, the prior-year comparative reported alongside it, the
# prior fiscal-year-end balance sheet, and a cover-page instant dated AFTER the
# quarter end — the max(period_end) trap.
FUND_CIK = 1318605
FUND_FILED = "2026-05-20"
FUND_ACCN = 104581026000052
FUND_PERIOD_END = "2026-04-26"
FUND_PRIOR_END = "2025-04-27"
FUND_COVER_PAGE = "2026-05-15"  # dei cover page, dated after the fiscal quarter

_COMPANIES_DDL = """
CREATE TABLE IF NOT EXISTS companies (
    symbol TEXT NOT NULL PRIMARY KEY,
    cik TEXT NOT NULL,
    sic INTEGER,
    sic_desc TEXT,
    sector TEXT
)
"""


def _fact(
    tag: str,
    period_start: str,
    period_end: str,
    val: float,
    *,
    taxonomy: str = "us-gaap",
    unit: str = "USD",
    fy: int | None = 2027,
    fp: str | None = "Q1",
    form: str = "10-Q",
    filed: str = FUND_FILED,
    accn: int = FUND_ACCN,
) -> dict:
    return {
        "taxonomy": taxonomy, "tag": tag, "period_start": period_start,
        "period_end": period_end, "unit": unit, "val": val, "fy": fy, "fp": fp,
        "form": form, "filed": filed, "accn": accn,
    }


def _quarter_facts() -> list[dict]:
    return [
        _fact("Revenues", "2026-01-26", FUND_PERIOD_END, 81_615_000_000),
        _fact("Revenues", "2025-01-27", FUND_PRIOR_END, 44_062_000_000),
        _fact("NetIncomeLoss", "2026-01-26", FUND_PERIOD_END, 58_321_000_000),
        _fact("NetIncomeLoss", "2025-01-27", FUND_PRIOR_END, 18_775_000_000),
        _fact("Assets", "", FUND_PERIOD_END, 259_474_000_000),
        _fact("Assets", "", "2026-01-25", 206_803_000_000),  # prior FY-end
        _fact("StockholdersEquity", "", FUND_PERIOD_END, 195_474_000_000),
        # A real 10-Q's equity roll-forward DOES carry the prior-year quarter-end
        # instant (measured on NVDA's filing) — an instant a YoY guard must skip.
        _fact("StockholdersEquity", "", FUND_PRIOR_END, 83_843_000_000),
        _fact(
            "EntityCommonStockSharesOutstanding",
            "", FUND_COVER_PAGE, 24_400_000_000,
            taxonomy="dei", unit="shares",
        ),
    ]


def _seed_edgar(
    tmp_path: Path,
    *,
    facts: list[dict] | None = None,
    symbol: str | None = SYMBOL,
    cik: int = FUND_CIK,
) -> tuple[Path, Path]:
    """Temp (facts_db, fundamentals_db) on the REAL schemas. ``symbol=None`` seeds
    the company table with no row for the episode's symbol (unmapped)."""
    facts_db = tmp_path / "edgar_facts.db"
    fund_db = tmp_path / "fundamentals.db"
    for path in (facts_db, fund_db):
        path.unlink(missing_ok=True)  # re-seeding one tmp_path with a different set
    rows = _quarter_facts() if facts is None else facts

    with connect(facts_db) as conn:
        ensure_bulk_schema(conn)  # the real corpus schema, not a hand copy
        conn.execute(
            "INSERT OR REPLACE INTO filers(cik, entity_name) VALUES (?, ?)",
            (cik, "Fixture Inc"),
        )
        concept_ids: dict[tuple[str, str], int] = {}
        for row in rows:
            key = (row["taxonomy"], row["tag"])
            if key not in concept_ids:
                concept_ids[key] = len(concept_ids) + 1
                conn.execute(
                    "INSERT INTO concepts(id, taxonomy, tag, label, description) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (concept_ids[key], row["taxonomy"], row["tag"], row["tag"], None),
                )
            conn.execute(
                "INSERT OR REPLACE INTO facts(cik, concept_id, period_end, filed, "
                "unit, period_start, accn, fy, fp, form, frame, val) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cik, concept_ids[key], row["period_end"], row["filed"],
                    row["unit"], row["period_start"], row["accn"], row["fy"],
                    row["fp"], row["form"], None, float(row["val"]),
                ),
            )
        conn.commit()

    with connect(fund_db) as conn:
        conn.execute(_COMPANIES_DDL)
        if symbol is not None:
            conn.execute(
                "INSERT OR REPLACE INTO companies(symbol, cik, sic, sic_desc, sector) "
                "VALUES (?, ?, ?, ?, ?)",
                (symbol, str(cik), 3711, "Motor Vehicles", "Manufacturing"),
            )
        conn.commit()
    return facts_db, fund_db


@pytest.fixture
def watermark(monkeypatch: pytest.MonkeyPatch):
    """Set the §3 ingest watermark. Its real reader lands on a parallel branch, so
    every fundamentals test states the watermark it means to test — including
    ``None`` (never checked), which must never silently depend on the import."""

    def _set(value: dict | None) -> None:
        monkeypatch.setattr(dossier_mod, "_edgar_ingest_watermark", lambda cik: value)

    return _set


def _checked(day: str, *, latest_filed: str | None = None) -> dict:
    # ``source`` is free-form (nightly / bulk / …) and is never branched on —
    # ``checked_at`` and ``latest_filed`` are the fields the angle reads.
    return {
        "checked_at": f"{day}T11:30:00+00:00",
        "source": "nightly",
        "latest_filed": latest_filed,
    }


def _fund_claims(dossier: dict) -> list[dict]:
    return [c for c in dossier["claims"] if c["angle"] == "fundamentals"]


def _build_fund(
    db: Path, tmp_path: Path, *, facts: list[dict] | None = None,
    symbol: str | None = SYMBOL, now: datetime = NOW,
    facts_db: Path | None = None, fund_db: Path | None = None,
) -> dict:
    # The episode dict, not a seeded row: the angle reads none of the episode's
    # stored state, and these tests build repeatedly against one db fixture.
    episode = {
        "id": EP_ID, "symbol": SYMBOL, "market_day": MARKET_DAY,
        "anchor_price": "245.00", "market_shock": 0,
    }
    if facts_db is None or fund_db is None:
        facts_db, fund_db = _seed_edgar(tmp_path, facts=facts, symbol=symbol)
    return build_dossier(
        episode,
        db_path=db,
        macro_db_path=tmp_path / "absent_macro.db",
        facts_db_path=facts_db,
        fundamentals_db_path=fund_db,
        now=now,
    )


def test_fundamentals_angle_lands_before_context(db: Path, tmp_path: Path, watermark) -> None:
    """D5: new angle, claim_id prefix, and its fixed ANGLE_ORDER position."""
    watermark(_checked("2026-07-20"))
    assert ANGLE_ORDER.index("fundamentals") == ANGLE_ORDER.index("news") + 1
    assert ANGLE_ORDER.index("fundamentals") == ANGLE_ORDER.index("context") - 1
    assert ANGLE_ORDER[-1] == "evidence"  # D2 truncation absorber stays last

    claims = _fund_claims(_build_fund(db, tmp_path))
    assert len(claims) == 4  # revenue, net income, assets, equity
    assert [c["claim_id"] for c in claims] == [f"fundamentals:{i}" for i in range(4)]
    assert all(c["statement"].startswith("fundamentals ") for c in claims)


def test_fundamentals_selects_fiscal_period_not_latest_period_end(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: the reporting period is the fiscal quarter end, NOT max(period_end).

    The same filing carries a cover-page instant dated 2026-05-15 — later than the
    2026-04-26 quarter end. Taking the filing's max would mislabel every claim.
    """
    watermark(_checked("2026-07-20"))
    claims = _fund_claims(_build_fund(db, tmp_path))
    assert claims
    for claim in claims:
        assert f"ended {FUND_PERIOD_END}" in claim["statement"]
        assert FUND_COVER_PAGE not in claim["statement"]
    # The reported values are the quarter's, not a comparative's.
    revenue = next(c for c in claims if "revenue" in c["statement"])
    assert revenue["value"] == 81_615_000_000
    assets = next(c for c in claims if "assets" in c["statement"])
    assert assets["value"] == 259_474_000_000  # not the 2026-01-25 comparative


def test_fundamentals_quarter_wins_over_year_to_date(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: a Q3 10-Q reports 3-month AND 9-month revenue ending the same day."""
    watermark(_checked("2026-07-20"))
    facts = [
        _fact("Revenues", "2026-04-01", "2026-06-30", 28_236_000_000, fp="Q2"),
        _fact("Revenues", "2026-01-01", "2026-06-30", 55_400_000_000, fp="Q2"),  # YTD
        _fact("Revenues", "2025-04-01", "2025-06-30", 22_496_000_000, fp="Q2"),
        _fact("Assets", "", "2026-06-30", 148_524_000_000, fp="Q2"),
    ]
    claims = _fund_claims(_build_fund(db, tmp_path, facts=facts))
    revenue = next(c for c in claims if "revenue" in c["statement"])
    assert revenue["value"] == 28_236_000_000  # the quarter, never the YTD roll-up


def test_fundamentals_annual_filing_picks_the_year(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: a 10-K's reporting period is the year, and it is labelled FY."""
    watermark(_checked("2026-07-20"))
    facts = [
        _fact(
            "Revenues", "2025-07-01", "2026-06-30", 331_839_000_000,
            fy=2026, fp="FY", form="10-K",
        ),
        _fact(
            "Revenues", "2026-04-01", "2026-06-30", 90_000_000_000,
            fy=2026, fp="FY", form="10-K",
        ),  # some filers also tag the Q4 stub in the 10-K
        _fact(
            "Revenues", "2024-07-01", "2025-06-30", 281_724_000_000,
            fy=2026, fp="FY", form="10-K",
        ),
    ]
    claims = _fund_claims(_build_fund(db, tmp_path, facts=facts))
    revenue = next(c for c in claims if "revenue" in c["statement"])
    assert revenue["value"] == 331_839_000_000
    assert "FY2026 ended 2026-06-30 (10-K filed 2026-05-20)" in revenue["statement"]


def test_fundamentals_names_period_and_corpus_asof(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: every claim names its fiscal period AND when we last checked SEC."""
    watermark(_checked("2026-07-20"))
    claims = _fund_claims(_build_fund(db, tmp_path))
    assert claims
    for claim in claims:
        assert "Q1 FY2027 ended 2026-04-26 (10-Q filed 2026-05-20)" in claim["statement"]
        assert "[SEC checked 2026-07-20]" in claim["statement"]


def test_fundamentals_fresh_when_recently_checked_despite_old_filing(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5 (the point of the round): freshness tracks OUR CHECK, not the filing.

    The 10-Q was filed 61 days before ``now`` and is still the company's current
    reported state — a day-count horizon on the filing would be the wrong tool.
    """
    watermark(_checked("2026-07-20"))
    claims = _fund_claims(_build_fund(db, tmp_path))
    assert claims and all(c["fresh"] is True for c in claims)
    summary = _build_fund(db, tmp_path)["summary"]
    fund_lines = [ln for ln in summary.splitlines() if ln.startswith("[fundamentals]")]
    assert fund_lines and not any("[stale]" in ln for ln in fund_lines)


def test_fundamentals_never_checked_says_recency_unknown(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: watermark None -> not fresh, and the statement says we never looked.

    "No newer filing exists" and "we have not checked" are the same observation
    from inside the store; the claim must never imply the first one.
    """
    watermark(None)
    dossier = _build_fund(db, tmp_path)
    claims = _fund_claims(dossier)
    assert claims and all(c["fresh"] is False for c in claims)
    for claim in claims:
        assert "[SEC recency unknown — corpus never checked]" in claim["statement"]
    fund_lines = [
        ln for ln in dossier["summary"].splitlines() if ln.startswith("[fundamentals]")
    ]
    assert fund_lines and all("[stale]" in ln for ln in fund_lines)


def test_fundamentals_stale_watermark_not_fresh(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: the check ages out at EDGAR_CHECK_MAX_AGE_DAYS; the boundary is pinned."""
    watermark(_checked("2026-07-17"))  # gap == 3 == the horizon
    edge = _fund_claims(_build_fund(db, tmp_path))
    assert edge and all(c["fresh"] is True for c in edge)
    assert EDGAR_CHECK_MAX_AGE_DAYS == 3

    watermark(_checked("2026-07-16"))  # gap == 4 — one day past
    past = _fund_claims(_build_fund(db, tmp_path))
    assert past and all(c["fresh"] is False for c in past)
    assert all("[SEC not checked since 2026-07-16]" in c["statement"] for c in past)


def test_fundamentals_corpus_behind_sec_not_fresh(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: a recent check that SAW a filing we never ingested is not "latest"."""
    watermark(_checked("2026-07-20", latest_filed="2026-07-19"))
    claims = _fund_claims(_build_fund(db, tmp_path))
    assert claims and all(c["fresh"] is False for c in claims)
    assert all(
        "corpus behind SEC (filings through 2026-07-19 not ingested)" in c["statement"]
        for c in claims
    )


def test_fundamentals_direction_is_never_directional(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: reported financials are context — never a bullish/bearish call."""
    watermark(_checked("2026-07-20"))
    dossier = _build_fund(db, tmp_path)
    claims = _fund_claims(dossier)
    assert claims and all(c["direction"] == "none" for c in claims)
    assert all(c["contradicts"] == [] for c in claims)


def test_fundamentals_yoy_is_labelled_derived_and_degrades(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: YoY comes from the same filing's comparative, is labelled derived, and
    is simply absent when the comparison period is not reported."""
    watermark(_checked("2026-07-20"))
    claims = _fund_claims(_build_fund(db, tmp_path))
    revenue = next(c for c in claims if "revenue" in c["statement"])
    assert "YoY +85.2% (derived vs 2025-04-27)" in revenue["statement"]
    net_income = next(c for c in claims if "net income" in c["statement"])
    assert "YoY +210.6% (derived vs 2025-04-27)" in net_income["statement"]
    # Balance-sheet instants get NO YoY even when the filing carries a
    # prior-year-dated instant (equity's roll-forward does): an instant is a
    # point in time, and a 10-Q's own comparative is the prior FISCAL-YEAR END.
    assets = next(c for c in claims if "assets" in c["statement"])
    assert "YoY" not in assets["statement"]
    equity = next(c for c in claims if "equity" in c["statement"])
    assert "YoY" not in equity["statement"]
    assert equity["value"] == 195_474_000_000

    no_prior = [f for f in _quarter_facts() if f["period_end"] != FUND_PRIOR_END]
    degraded = _fund_claims(_build_fund(db, tmp_path, facts=no_prior))
    revenue = next(c for c in degraded if "revenue" in c["statement"])
    assert "YoY" not in revenue["statement"]
    assert revenue["value"] == 81_615_000_000  # the level survives


def test_fundamentals_untagged_metric_is_silent(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: a concept the filer does not tag (banks skip Revenues) drops out — we
    never substitute a figure we computed ourselves."""
    watermark(_checked("2026-07-20"))
    facts = [f for f in _quarter_facts() if f["tag"] != "Revenues"]
    claims = _fund_claims(_build_fund(db, tmp_path, facts=facts))
    assert len(claims) == 3
    assert not any("revenue" in c["statement"] for c in claims)


def test_fundamentals_absent_dbs_degrade_without_creating_them(
    db: Path, tmp_path: Path, watermark
) -> None:
    """D5: no data/ at all -> zero claims, no raise, and NO stub sqlite files.

    A read path without the ``is_file()`` guard creates an empty DB (D4's review
    finding); on a 16 GB shared corpus path that is worse than a wrong answer.
    """
    watermark(_checked("2026-07-20"))
    # Parent dir EXISTS on purpose: a writable connect would happily create the
    # stub here, so the assertion below actually tests the read-only guards.
    facts_db = tmp_path / "absent_edgar_facts.db"
    fund_db = tmp_path / "absent_fundamentals.db"
    dossier = _build_fund(db, tmp_path, facts_db=facts_db, fund_db=fund_db)
    assert _fund_claims(dossier) == []
    assert not facts_db.exists()
    assert not fund_db.exists()

    # Corpus present but the symbol is not in the curated mapping -> same silence.
    present_facts, present_fund = _seed_edgar(tmp_path, symbol=None)
    unmapped = _build_fund(
        db, tmp_path, facts_db=present_facts, fund_db=present_fund
    )
    assert _fund_claims(unmapped) == []


def test_fundamentals_deterministic(db: Path, tmp_path: Path, watermark) -> None:
    """D5: same episode + same now -> identical claim_ids and statements."""
    watermark(_checked("2026-07-20"))
    facts_db, fund_db = _seed_edgar(tmp_path)
    first = _build_fund(db, tmp_path, facts_db=facts_db, fund_db=fund_db)
    second = _build_fund(db, tmp_path, facts_db=facts_db, fund_db=fund_db)
    assert first["claims"] == second["claims"]
    assert [c["claim_id"] for c in _fund_claims(first)] == [
        c["claim_id"] for c in _fund_claims(second)
    ]


# ------------------------------------------------------- perception resource (wire)


def test_perception_dossier_resource_builds_from_open_episode(db: Path) -> None:
    """The `dossier` resource returns the open episode's claims, built fresh."""
    _seed_full(db)
    result = asyncio.run(perception_query("dossier", {"symbol": SYMBOL}, db_path=db))
    assert result.get("error") is None
    assert result["resource"] == "dossier"
    rows = result["rows"]
    assert result["count"] == len(rows) > 0
    assert {row["angle"] for row in rows} >= {"evidence", "history"}
    assert all(row["claim_id"] and row["statement"] for row in rows)
    # Claims are the complete matrix — never row-capped (a dropped claim would be
    # invisible AND uncitable for a consumer citing claim_ids).
    direct = build_dossier(_episode_row(db), db_path=db)
    assert len(rows) == len(direct["claims"])


def test_perception_dossier_requires_symbol_and_open_episode(db: Path) -> None:
    missing = asyncio.run(perception_query("dossier", {}, db_path=db))
    assert "symbol" in str(missing.get("error"))
    unknown = asyncio.run(perception_query("dossier", {"symbol": "ZZZQ"}, db_path=db))
    assert "no open fusion episode" in str(unknown.get("error"))


def test_perception_dossier_ignores_closed_episode(db: Path) -> None:
    """One-open-episode-per-symbol: a closed episode is not the live question."""
    _seed_episode(db)
    with connect(db) as conn:
        conn.execute(
            "UPDATE fusion_episodes SET state = 'closed' WHERE id = ?", (EP_ID,)
        )
        conn.commit()
    result = asyncio.run(perception_query("dossier", {"symbol": SYMBOL}, db_path=db))
    assert "no open fusion episode" in str(result.get("error"))


def test_perception_dossier_read_path_never_writes(db: Path) -> None:
    """Ephemeral by design: put_dossier stays REASON-lane-only."""
    _seed_full(db)
    asyncio.run(perception_query("dossier", {"symbol": SYMBOL}, db_path=db))
    with connect(db) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idea_dossiers'"
        ).fetchone()
    assert row is None


def _episode_row(db: Path) -> dict:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM fusion_episodes WHERE id = ?", (EP_ID,)
        ).fetchone()
    return dict(row)


# ---------------------------------------------- episodes resource (discovery surface)


def _set_episode(db: Path, ep_id: str, **cols) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    with connect(db) as conn:
        conn.execute(
            f"UPDATE fusion_episodes SET {sets} WHERE id = ?", (*cols.values(), ep_id)
        )
        conn.commit()


def test_perception_episodes_lists_open_ranked_by_score(db: Path) -> None:
    """`episodes` is the discovery surface: open stacks only, score-first."""
    _seed_episode(db)
    _seed_episode(db, ep_id="ep_nvda", symbol="NVDA")
    _seed_episode(db, ep_id="ep_amd", symbol="AMD")
    _set_episode(db, "ep_nvda", score=5.0, direction_state="bull")
    _set_episode(db, "ep_amd", state="closed")
    _seed_dot(
        db,
        dot_id="dot_ep_list",
        component="flow_premium",
        group="flow",
        direction="bull",
        points=1.0,
        raw_value="1",
        ts=_utc(18),
    )
    result = asyncio.run(perception_query("episodes", {}, db_path=db))
    assert result.get("error") is None
    rows = result["rows"]
    assert [r["symbol"] for r in rows] == ["NVDA", SYMBOL]  # closed AMD absent
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol[SYMBOL]["dot_count"] == 1
    assert by_symbol["NVDA"]["direction_state"] == "bull"
    assert by_symbol["NVDA"]["id"] == "ep_nvda"


def test_perception_episodes_symbol_filter_and_limit(db: Path) -> None:
    _seed_episode(db)
    _seed_episode(db, ep_id="ep_nvda", symbol="NVDA")
    _set_episode(db, "ep_nvda", score=5.0)
    one = asyncio.run(perception_query("episodes", {"symbol": "tsla"}, db_path=db))
    assert [r["symbol"] for r in one["rows"]] == [SYMBOL]
    top = asyncio.run(perception_query("episodes", {"limit": 1}, db_path=db))
    assert [r["symbol"] for r in top["rows"]] == ["NVDA"]
    # The cap is never silent: the envelope still says how many exist in all.
    assert top["summary"]["open_total"] == 2


def test_perception_episodes_alias_and_dossier_pointer(db: Path) -> None:
    """Reachable by its natural names, and the dossier's episode-missing error
    hands the agent this resource instead of a dead end."""
    empty = asyncio.run(perception_query("evidence_stacks", {}, db_path=db))
    assert empty["resource"] == "episodes"
    assert empty["count"] == 0
    assert empty["summary"]["open_total"] == 0
    missing = asyncio.run(perception_query("dossier", {"symbol": "ZZZQ"}, db_path=db))
    assert "'episodes'" in str(missing.get("error"))
