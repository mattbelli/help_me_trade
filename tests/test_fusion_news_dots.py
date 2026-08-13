"""News → fusion catalyst dots (board news-catalyst-dots).

The attach-only news lane: headlines from ``news_items`` become catalyst-group
EVENT dots on OPEN episodes. Headlines are FACTS — direction is always ``none``,
so news adds catalyst points and breadth without ever touching a directional
mass or the z dual gate. These tests pin the burst buckets, the roundup cap, the
SESSION-clock freshness window (a pre-market headline is fresh at the open), the
expiry-zeroing, the per-symbol dedup key, and the bus-cursor contract.

Times are ET-day-aware by construction (ZoneInfo), never ``date.today()`` —
wall-clock date math flakes between 00:00 and 04:00 UTC.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from db.base import connect
from db.chart_features import init_chart_features
from db.earnings_calendar import upsert_earnings
from db.fusion import (
    conservation_counts,
    episode_dots,
    get_cursor,
    init_fusion_tables,
    put_atr,
    set_cursor,
)
from db.lane_stats import day as lane_day
from db.lane_stats import et_today
from db.news import init_db as init_news
from db.news import upsert_news_item
from db.options_flow import insert_event as insert_flow
from signals.fusion import intake
from signals.fusion.intake import intake_once
from signals.fusion.scorer import score_decayed

ET = ZoneInfo("America/New_York")
MARKET_DAY = "2026-07-17"  # a Friday
NOW_ET = datetime(2026, 7, 17, 10, 30, tzinfo=ET)  # inside RTH
NOW = NOW_ET.astimezone(ZoneInfo("UTC"))
EVENT_TS = NOW.isoformat()


@pytest.fixture
def fusion_db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    init_chart_features(path)
    init_news(path)
    return path


def _news(
    news_id: str,
    symbols: list[str],
    *,
    published: datetime = NOW,
    headline: str = "Headline",
) -> dict[str, Any]:
    return {
        "id": news_id,
        "headline": headline,
        "summary": "",
        "url": f"https://example.test/{news_id}",
        "source": "benzinga",
        "author": "",
        "symbols": symbols,
        "image_url": None,
        "published_at": published.isoformat(),
        "updated_at": None,
    }


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


def _seed_actor(db: Path, *, symbol: str = "TSLA", event_id: str | None = None) -> None:
    put_atr(symbol, "2026-07-16", Decimal("12.50"), db_path=db)
    insert_flow(_flow(event_id or f"flow-{symbol}", symbol=symbol), db)


def _run_intake(db: Path) -> dict:
    return intake_once(db_path=db, price_lookup=lambda _s: Decimal("320.25"))


def _open_episode(db: Path, symbol: str) -> str:
    episode = intake._ensure_episode(symbol, MARKET_DAY, EVENT_TS, False, db)
    return str(episode["id"])


def _news_dots(db: Path, episode_id: str) -> list[dict]:
    with connect(db) as conn:
        rows = conn.execute(
            """
            SELECT * FROM evidence_dots
            WHERE episode_id = ? AND component = 'news'
            ORDER BY source_ingest_seq
            """,
            (episode_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _meter(db: Path, counter: str) -> float:
    return lane_day("fusion_intake", et_today(), db_path=db).get(counter, 0.0)


# ------------------------------------------------------------ attach semantics


def test_headline_without_open_episode_mints_nothing(fusion_db: Path) -> None:
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)

    _run_intake(fusion_db)

    with connect(fusion_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_episodes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM evidence_dots").fetchone()[0] == 0
    assert get_cursor("news", db_path=fusion_db) == 1
    assert _meter(fusion_db, "news_no_open_episode") == 1.0


def test_news_dot_shape(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"], headline="Tesla opens a plant"), db_path=fusion_db)

    dirty, seq = intake._intake_news(NOW, fusion_db)

    assert dirty == {ep}
    assert seq == 1
    (dot,) = _news_dots(fusion_db, ep)
    assert dot["component"] == "news"
    assert dot["actor_group"] == "catalyst"
    assert dot["dot_kind"] == "event"
    assert dot["direction"] == "none"
    assert dot["ts"] == NOW.isoformat()
    assert dot["symbol"] == "TSLA"
    assert float(dot["points"]) == 0.5
    detail = json.loads(dot["detail_json"])
    assert detail["headline"] == "Tesla opens a plant"
    assert detail["source"] == "benzinga"
    assert detail["n_symbols"] == 1
    assert detail["count_in_window"] == 1
    assert _meter(fusion_db, "news_dot_minted") == 1.0


def test_one_headline_two_symbols_mints_two_distinct_dots(fusion_db: Path) -> None:
    # Regression on the shared-key ping-pong: the dedup UNIQUE carries no
    # episode_id, so a key without the symbol would move ONE row between them.
    tsla = _open_episode(fusion_db, "TSLA")
    nvda = _open_episode(fusion_db, "NVDA")
    upsert_news_item(_news("n1", ["TSLA", "NVDA"]), db_path=fusion_db)

    dirty, _ = intake._intake_news(NOW, fusion_db)

    assert dirty == {tsla, nvda}
    (tsla_dot,) = _news_dots(fusion_db, tsla)
    (nvda_dot,) = _news_dots(fusion_db, nvda)
    assert tsla_dot["id"] != nvda_dot["id"]
    assert tsla_dot["source_event_id"] == "n1:TSLA"
    assert nvda_dot["source_event_id"] == "n1:NVDA"


# --------------------------------------------------------------------- scoring


def test_burst_count_buckets_and_group_score(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    for i in range(5):
        upsert_news_item(
            _news(f"n{i}", ["TSLA"], published=NOW + timedelta(minutes=i)),
            db_path=fusion_db,
        )

    intake._intake_news(NOW + timedelta(minutes=5), fusion_db)

    points = [float(dot["points"]) for dot in _news_dots(fusion_db, ep)]
    assert points == [0.5, 0.75, 0.75, 1.0, 1.0]
    state = intake._rescore(ep, fusion_db)
    assert state["group_scores"]["catalyst"] == 1.0


def test_roundup_headline_mints_annotate_only_zeros(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    roundup = ["TSLA"] + [f"SYM{i}" for i in range(19)]
    upsert_news_item(_news("n1", roundup, headline="20 stocks moving today"), db_path=fusion_db)

    intake._intake_news(NOW, fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.0
    assert json.loads(dot["detail_json"])["n_symbols"] == 20
    state = intake._rescore(ep, fusion_db)
    assert "catalyst" not in state["group_scores"]
    assert _meter(fusion_db, "news_roundup_zeroed") == 1.0


def test_stale_news_dot_is_zeroed_not_frozen(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    intake._intake_news(NOW, fusion_db)
    assert float(_news_dots(fusion_db, ep)[0]["points"]) == 0.5

    later = NOW + timedelta(hours=3)  # 13:30 ET — 180 session-minutes on
    dirty = intake._expire_news_dots(later, fusion_db)

    assert dirty == {ep}
    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.0
    assert json.loads(dot["detail_json"])["expired"] is True
    state = intake._rescore(ep, fusion_db)
    assert "catalyst" not in state["group_scores"]


def test_old_headline_on_the_arrival_lane_scores_zero(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(
        _news("n-old", ["TSLA"], published=NOW - timedelta(days=3)), db_path=fusion_db
    )

    intake._intake_news(NOW, fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.0


def test_premarket_headline_is_fresh_on_the_session_clock(fusion_db: Path) -> None:
    # Wall-clock math would call this 2.5h old and score 0; the session clock
    # counts only the one minute of RTH between 09:30 and 09:31.
    ep = _open_episode(fusion_db, "TSLA")
    published = datetime(2026, 7, 17, 7, 0, tzinfo=ET)
    upsert_news_item(_news("n1", ["TSLA"], published=published), db_path=fusion_db)

    intake._intake_news(datetime(2026, 7, 17, 9, 31, tzinfo=ET), fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.5


def test_catalyst_is_shared_not_additive(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    upsert_earnings([{"symbol": "TSLA", "date": "2026-07-22"}], db_path=fusion_db)  # 3 wd
    _run_intake(fusion_db)
    ep = _episode_of(fusion_db, "TSLA")
    for i in range(4):
        upsert_news_item(
            _news(f"n{i}", ["TSLA"], published=NOW + timedelta(minutes=i)), db_path=fusion_db
        )

    intake._intake_news(NOW + timedelta(minutes=4), fusion_db)
    state = intake._rescore(ep, fusion_db)

    earnings = [
        d for d in episode_dots(ep, db_path=fusion_db) if d["component"] == "earnings_proximity"
    ]
    assert float(earnings[0]["points"]) == pytest.approx(0.6)
    assert max(float(d["points"]) for d in _news_dots(fusion_db, ep)) == 1.0
    assert state["group_scores"]["catalyst"] == 1.0


def _episode_of(db: Path, symbol: str) -> str:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT id FROM fusion_episodes WHERE symbol = ?", (symbol,)
        ).fetchone()
    assert row is not None, f"no episode for {symbol}"
    return str(row["id"])


# -------------------------------------------------- backfill (cursor ordering)


def _consume(db: Path, now: datetime) -> None:
    """One arrival-lane pass + the cursor advance intake_once would do."""
    _, seq = intake._intake_news(now, db)
    if seq:
        set_cursor("news", seq, db_path=db)


def test_headline_that_leads_the_flow_burst_is_backfilled(fusion_db: Path) -> None:
    # The academically valuable case: the headline lands BEFORE the flow burst
    # that opens the episode. The cursor consumed it against no open episode, so
    # without the backfill read this dot never existed.
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    _consume(fusion_db, NOW)
    assert _meter(fusion_db, "news_no_open_episode") == 1.0
    with connect(fusion_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evidence_dots").fetchone()[0] == 0

    ep = _open_episode(fusion_db, "TSLA")  # the burst opens it minutes later
    dirty, seq = intake._intake_news(NOW + timedelta(minutes=5), fusion_db)

    assert dirty == {ep}
    assert seq == 0  # nothing new on the arrival lane — the cursor stays put
    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.5
    assert json.loads(dot["detail_json"])["count_in_window"] == 1
    assert _meter(fusion_db, "news_backfill_minted") == 1.0
    assert _meter(fusion_db, "news_dot_minted") == 0.0


def test_stale_headline_is_never_backfilled(fusion_db: Path) -> None:
    # Inside the 7-day SQL bound, outside the 120-session-minute window: the
    # candidate is READ and then rejected by _news_fresh, never minted at 0.
    published = NOW - timedelta(days=2)
    upsert_news_item(_news("n-old", ["TSLA"], published=published), db_path=fusion_db)
    _consume(fusion_db, published)
    ep = _open_episode(fusion_db, "TSLA")

    dirty, _ = intake._intake_news(NOW, fusion_db)

    assert dirty == set()
    assert _news_dots(fusion_db, ep) == []
    assert _meter(fusion_db, "news_backfill_minted") == 0.0


def test_backfill_never_double_mints_an_attached_pair(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    _consume(fusion_db, NOW)
    assert _meter(fusion_db, "news_dot_minted") == 1.0

    dirty, _ = intake._intake_news(NOW + timedelta(minutes=1), fusion_db)

    assert dirty == set()
    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.5  # bucket not inflated by a self-recount
    assert _meter(fusion_db, "news_backfill_minted") == 0.0
    assert _meter(fusion_db, "news_dot_minted") == 1.0


def test_roundup_backfills_at_zero_points(fusion_db: Path) -> None:
    roundup = ["TSLA"] + [f"SYM{i}" for i in range(19)]
    upsert_news_item(_news("n1", roundup, headline="20 stocks moving today"), db_path=fusion_db)
    _consume(fusion_db, NOW)
    ep = _open_episode(fusion_db, "TSLA")

    dirty, _ = intake._intake_news(NOW + timedelta(minutes=5), fusion_db)

    assert dirty == {ep}
    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.0
    assert _meter(fusion_db, "news_backfill_minted") == 1.0
    assert _meter(fusion_db, "news_roundup_zeroed") == 1.0
    assert "catalyst" not in intake._rescore(ep, fusion_db)["group_scores"]


# ------------------------------------------------------------------- invariants


def test_news_dot_leaves_the_z_gate_untouched(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_of(fusion_db, "TSLA")
    before = score_decayed(episode_dots(ep, db_path=fusion_db), NOW)["thesis"]

    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    intake._intake_news(NOW, fusion_db)

    after = score_decayed(episode_dots(ep, db_path=fusion_db), NOW)["thesis"]
    assert after == before


def test_news_never_fabricates_agreement(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_of(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    intake._intake_news(NOW, fusion_db)

    state = intake._rescore(ep, fusion_db)

    assert state["group_scores"]["catalyst"] == 0.5
    assert state["agreement_bonus"] == 0.0


def test_conservation_law_is_unchanged_by_the_news_lane(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)

    _run_intake(fusion_db)

    counts = conservation_counts(db_path=fusion_db)
    assert counts["inputs"] == counts["dispositions"] == 1
    with connect(fusion_db) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM fusion_dispositions WHERE source_store = 'news'"
        ).fetchone()[0]
    assert rows == 0


# ----------------------------------------------------------------- bus cursor


def test_news_cursor_is_seeded_and_replay_mints_nothing_new(fusion_db: Path) -> None:
    with connect(fusion_db) as conn:
        row = conn.execute(
            "SELECT last_ingest_seq FROM bus_cursors WHERE store = 'news'"
        ).fetchone()
    assert row is not None, "init_fusion_tables must seed the attach-lane cursor"
    assert int(row["last_ingest_seq"]) == 0

    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_of(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    _run_intake(fusion_db)
    first = get_cursor("news", db_path=fusion_db)
    assert first == 1
    assert _meter(fusion_db, "news_dot_minted") == 1.0

    _run_intake(fusion_db)

    assert get_cursor("news", db_path=fusion_db) == first
    assert _meter(fusion_db, "news_dot_minted") == 1.0
    assert len(_news_dots(fusion_db, ep)) == 1


# ---------------------------------------------------------------- ADVERSARIAL


def test_crash_replay_does_not_inflate_the_burst_count(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    intake._intake_news(NOW, fusion_db)
    assert float(_news_dots(fusion_db, ep)[0]["points"]) == 0.5

    # Crash after the dot batch committed but BEFORE set_cursor('news') — the
    # next pass re-polls the same row and must NOT count the headline's own dot.
    intake._intake_news(NOW, fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.5
    assert json.loads(dot["detail_json"])["count_in_window"] == 1


def test_duplicate_symbol_tags_count_once(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    with connect(fusion_db) as conn:
        conn.execute(
            """
            INSERT INTO news_items(id, ingest_seq, headline, symbols, published_at, received_at)
            VALUES ('n1', 1, 'Dup tags', '["TSLA","TSLA","TSLA"]', ?, ?)
            """,
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.commit()

    intake._intake_news(NOW, fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.5
    assert json.loads(dot["detail_json"])["n_symbols"] == 1
    assert _meter(fusion_db, "news_dot_minted") == 1.0


def test_malformed_symbols_json_mints_no_phantom_tickers(fusion_db: Path) -> None:
    # A bare JSON string iterates CHARACTERS — 'T' is a real ticker.
    _open_episode(fusion_db, "T")
    with connect(fusion_db) as conn:
        conn.execute(
            """
            INSERT INTO news_items(id, ingest_seq, headline, symbols, published_at, received_at)
            VALUES ('n1', 1, 'Bad symbols', '"TSLA"', ?, ?)
            """,
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.commit()

    intake._intake_news(NOW, fusion_db)

    with connect(fusion_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evidence_dots WHERE component = 'news'"
        ).fetchone()[0] == 0


def test_future_dated_headline_never_scores(fusion_db: Path) -> None:
    # Clock skew / a corrupt stamp must not pin catalyst forever: score_dots
    # takes max(points) with no decay over the episode's whole 15-session life.
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(
        _news("n1", ["TSLA"], published=NOW + timedelta(days=400)), db_path=fusion_db
    )

    intake._intake_news(NOW, fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.0


def test_small_clock_skew_still_counts(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(
        _news("n1", ["TSLA"], published=NOW + timedelta(seconds=30)), db_path=fusion_db
    )

    intake._intake_news(NOW, fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.5


def test_zero_point_dot_reports_no_burst_count(fusion_db: Path) -> None:
    # count_in_window must mean the same thing on every dot: the burst that
    # produced the points. A dot that scores nothing counts nothing (the value
    # the expiry sweep also writes) — including a roundup landing on an episode
    # that already carries a live burst.
    ep = _open_episode(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    upsert_news_item(
        _news("n-old", ["TSLA"], published=NOW - timedelta(days=3)), db_path=fusion_db
    )
    roundup = ["TSLA"] + [f"SYM{i}" for i in range(19)]
    upsert_news_item(_news("n-round", roundup), db_path=fusion_db)

    intake._intake_news(NOW, fusion_db)

    # ordered by ingest_seq: the fresh headline, the backfill, the roundup
    assert [
        (float(dot["points"]), json.loads(dot["detail_json"])["count_in_window"])
        for dot in _news_dots(fusion_db, ep)
    ] == [(0.5, 1), (0.0, 0), (0.0, 0)]


def test_news_never_adds_a_group_direction(fusion_db: Path) -> None:
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_of(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)
    intake._intake_news(NOW, fusion_db)

    state = intake._rescore(ep, fusion_db)

    assert "catalyst" in state["group_scores"]
    assert "catalyst" not in state["group_directions"]


def test_friday_headline_is_still_fresh_at_mondays_open(fusion_db: Path) -> None:
    # The session clock's deliberate consequence: overnight and weekend gaps
    # contribute zero, so a late-Friday headline rides into Monday.
    ep = _open_episode(fusion_db, "TSLA")
    friday = datetime(2026, 7, 17, 15, 59, tzinfo=ET)
    upsert_news_item(_news("n1", ["TSLA"], published=friday), db_path=fusion_db)

    intake._intake_news(datetime(2026, 7, 20, 9, 31, tzinfo=ET), fusion_db)

    (dot,) = _news_dots(fusion_db, ep)
    assert float(dot["points"]) == 0.5


def test_existing_deployed_db_gains_the_news_cursor_row(tmp_path: Path) -> None:
    # The deployed DB has bus_cursors with only the two actor stores; init must
    # add the attach-lane row (set_cursor is a bare UPDATE that would no-op).
    path = tmp_path / "legacy.db"
    init_fusion_tables(path)
    with connect(path) as conn:
        conn.execute("DELETE FROM bus_cursors WHERE store = 'news'")
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM bus_cursors WHERE store = 'news'"
        ).fetchone()[0] == 0

    init_fusion_tables(path)

    with connect(path) as conn:
        row = conn.execute(
            "SELECT last_ingest_seq FROM bus_cursors WHERE store = 'news'"
        ).fetchone()
    assert row is not None and int(row["last_ingest_seq"]) == 0


def test_episode_closed_mid_pass_is_not_carded_or_investigated(
    fusion_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signals.fusion import cards as cards_mod
    from signals.fusion import investigate as investigate_mod

    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_of(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)

    real_expire = intake._expire_news_dots

    def closing(now: datetime, db_path: Path) -> set[str]:
        # The lifecycle sweep runs in another process; it can close an episode
        # between the mint and the rescore tail.
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE fusion_episodes SET state = 'closed', closed_at = ? WHERE id = ?",
                (EVENT_TS, ep),
            )
            conn.commit()
        return real_expire(now, db_path)

    carded: list[str] = []
    investigated: list[str] = []
    monkeypatch.setattr(intake, "_expire_news_dots", closing)
    monkeypatch.setattr(
        cards_mod, "update_stack_card", lambda e, s, **kw: carded.append(str(e["id"]))
    )
    monkeypatch.setattr(
        investigate_mod,
        "evaluate_investigation",
        lambda e, s, **kw: investigated.append(str(e["id"])),
    )

    _run_intake(fusion_db)

    assert carded == []
    assert investigated == []


def test_burst_points_are_a_moment_not_a_running_count(fusion_db: Path) -> None:
    """A dot's points are the burst count AT MINT and are only ever zeroed.

    Contract, not accident: two of a four-headline burst aging out leaves the
    fourth dot at 1.0 until it too ages out, so catalyst can read one bucket
    above the live headline count for at most one window. Re-deriving every
    fresh dot's points each pass is the alternative and costs a write per dot
    per 60s pass — the write budget the lane exists to protect.
    """
    ep = _open_episode(fusion_db, "TSLA")
    early = [NOW, NOW + timedelta(minutes=5)]
    late = [NOW + timedelta(minutes=115), NOW + timedelta(minutes=118)]
    for i, published in enumerate(early + late):
        upsert_news_item(_news(f"n{i}", ["TSLA"], published=published), db_path=fusion_db)
    intake._intake_news(late[-1], fusion_db)
    assert [float(d["points"]) for d in _news_dots(fusion_db, ep)] == [0.5, 0.75, 0.75, 1.0]

    # 12:36 ET — the two morning headlines are out of the 120-session-minute
    # window, the two late ones are not.
    dirty = intake._expire_news_dots(NOW + timedelta(minutes=126), fusion_db)

    assert dirty == {ep}
    assert [float(d["points"]) for d in _news_dots(fusion_db, ep)] == [0.0, 0.0, 0.75, 1.0]
    assert intake._rescore(ep, fusion_db)["group_scores"]["catalyst"] == 1.0


def test_news_dirty_episode_spends_no_investigation_budget(fusion_db: Path) -> None:
    # News cannot add options_actor or structure to group_scores, so the Phase C
    # archetype check short-circuits before the budget is even read.
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_of(fusion_db, "TSLA")
    upsert_news_item(_news("n1", ["TSLA"]), db_path=fusion_db)

    _run_intake(fusion_db)

    state = intake._rescore(ep, fusion_db)
    assert "structure" not in state["group_scores"]
    with connect(fusion_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_investigations").fetchone()[0] == 0


def test_batch_limit_boundary_resumes_on_the_next_pass(fusion_db: Path) -> None:
    ep = _open_episode(fusion_db, "TSLA")
    for i in range(3):
        upsert_news_item(
            _news(f"n{i}", ["TSLA"], published=NOW + timedelta(minutes=i)), db_path=fusion_db
        )

    dirty, seq = intake._intake_news(NOW, fusion_db, batch_limit=2)
    assert (dirty, seq) == ({ep}, 2)
    assert len(_news_dots(fusion_db, ep)) == 2
    get_cursor("news", db_path=fusion_db)  # cursor advance is the caller's job
    from db.fusion import set_cursor

    set_cursor("news", seq, db_path=fusion_db)

    dirty, seq = intake._intake_news(NOW, fusion_db, batch_limit=2)

    assert (dirty, seq) == ({ep}, 3)
    assert [float(d["points"]) for d in _news_dots(fusion_db, ep)] == [0.5, 0.75, 0.75]
