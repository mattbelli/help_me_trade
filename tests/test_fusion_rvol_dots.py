"""rvol_ex_prints — the last unminted scorer component (board fusion-unminted-dots).

Pins: the ANNOTATE-ONLY invariant (background_crowd never scores, points always
0.0), the raw_value/ts contract lifecycle._latest_rvol depends on, the
prints subtraction, the day-volume read-cache's freshness + market-day fences,
and the unavailable states (never a frozen last-good reading, never 0.0 as if
it were a measurement).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from config.idea_engine import FUSION_RVOL_DAY_VOLUME_MAX_AGE_S
from db.base import connect
from db.chart_features import init_chart_features
from db.equity_tape import insert_event as insert_tape
from db.fusion import get_day_volume, init_fusion_tables, put_atr, put_day_volumes
from db.options_flow import insert_event as insert_flow
from signals.fusion import intake
from signals.fusion.intake import intake_once
from signals.volume_profile import N_BUCKETS, Profile, save_profile

MARKET_DAY = "2026-07-17"  # a Friday
EVENT_TS = "2026-07-17T14:30:00+00:00"  # 10:30 ET — 60 min in, bucket 12
OFFSET_TS = "2026-07-17T13:32:00+00:00"  # 09:32 ET — inside the latch window
BUCKET = 12
# cumfrac[12] = 13/78 = 1/6, so expected-by-now = 7_800_000 / 6 = 1_300_000.
MEDIAN_DAILY_TOTAL = 7_800_000.0
EXPECTED_BY_NOW = 1_300_000.0
DAY_VOLUME = 3_000_000
PREMARKET_OFFSET = 400_000  # → RTH-only 2_600_000 = 2.00x raw
PRINT_SHARES = 260_000  # → 2_340_000 ex-prints = 1.80x


@pytest.fixture
def fusion_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    init_chart_features(path)
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path / "profiles"))
    _clear_memos()
    return path


def _clear_memos() -> None:
    intake._RVOL_BUCKET_MEMO.clear()
    intake._RVOL_PRINTS_MEMO.clear()
    intake._RVOL_PROFILE_MEMO.clear()


def _save_profile(symbol: str = "TSLA", *, n_days: int = 20) -> None:
    save_profile(
        Profile(
            symbol=symbol,
            cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
            median_daily_total=MEDIAN_DAILY_TOTAL,
            n_days=n_days,
        )
    )


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


def _seed_actor(db: Path, *, symbol: str = "TSLA", event_id: str = "flow-1") -> None:
    put_atr(symbol, "2026-07-16", Decimal("12.50"), db_path=db)
    insert_flow(_flow(event_id, symbol=symbol), db)


def _seed_day_volume(
    db: Path,
    *,
    symbol: str = "TSLA",
    volume: int = DAY_VOLUME,
    offset: int | None = PREMARKET_OFFSET,
    offset_ts: str | None = OFFSET_TS,
    market_day: str = MARKET_DAY,
    as_of_ts: str = EVENT_TS,
) -> None:
    put_day_volumes(
        [
            (
                symbol,
                str(volume),
                None if offset is None else str(offset),
                None if offset is None else offset_ts,
                market_day,
                as_of_ts,
            )
        ],
        db_path=db,
    )


def _seed_print(db: Path, *, symbol: str = "TSLA", shares: int = PRINT_SHARES) -> None:
    insert_tape(
        {
            "id": f"tape-{symbol}",
            "ts": "2026-07-17T14:00:00+00:00",
            "symbol": symbol,
            "event_type": "dark_pool",
            "price": 320.0,
            "size": shares,
            "notional_usd": shares * 320.0,
            "direction": "buy_side",
            "created_at": "2026-07-17T14:00:00+00:00",
        },
        db,
    )


def _run_intake(db: Path) -> dict:
    return intake_once(db_path=db, price_lookup=lambda _s: Decimal("320.25"))


def _episode_id(db: Path, symbol: str = "TSLA") -> str:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT id FROM fusion_episodes WHERE symbol = ?", (symbol,)
        ).fetchone()
    assert row is not None, f"no episode for {symbol}"
    return str(row["id"])


def _rvol_dots(db: Path, episode_id: str) -> list[dict]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM evidence_dots WHERE episode_id = ? AND component = 'rvol_ex_prints'",
            (episode_id,),
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


# ------------------------------------------------------- day-volume read-cache


def test_day_volume_returns_rth_raw_and_the_offset_stamp(tmp_path: Path) -> None:
    db = tmp_path / "v.db"
    init_fusion_tables(db)
    _seed_day_volume(db)
    got = get_day_volume(
        "TSLA", max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S, as_of=EVENT_TS, db_path=db
    )
    # The stamp travels with the numbers: rth_volume counts FROM offset_ts, so
    # a caller subtracting prints has to window them from the same instant.
    assert got == (Decimal("2600000"), Decimal("3000000"), OFFSET_TS)


def test_day_volume_refuses_an_unlatched_offset(tmp_path: Path) -> None:
    # NULL offset = "no trustworthy premarket baseline today", NOT a zero
    # offset. Reading it as zero would publish day_volume (extended hours and
    # all) over an RTH-only baseline — an inflated reading, not a missing one.
    db = tmp_path / "v.db"
    init_fusion_tables(db)
    _seed_day_volume(db, offset=None)
    assert (
        get_day_volume(
            "TSLA",
            max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S,
            as_of=EVENT_TS,
            db_path=db,
        )
        is None
    )


def test_day_volume_refuses_an_offset_above_the_day_volume(tmp_path: Path) -> None:
    # Alpaca's snapshot serves the PREVIOUS session's dailyBar until a symbol's
    # first trade of the day, so a thin name can latch yesterday's full-day
    # total at 09:31 and then "shrink". Clamping that to zero published a
    # fabricated 0.0x RVOL for the rest of the session.
    db = tmp_path / "v.db"
    init_fusion_tables(db)
    _seed_day_volume(db, volume=120_000, offset=4_000_000)
    assert (
        get_day_volume(
            "TSLA",
            max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S,
            as_of=EVENT_TS,
            db_path=db,
        )
        is None
    )


def test_day_volume_survives_garbage_rows(tmp_path: Path) -> None:
    db = tmp_path / "v.db"
    init_fusion_tables(db)
    _seed_day_volume(db)
    for column, value in (
        ("day_volume", "not-a-number"),
        ("premarket_offset", "  "),
        ("as_of_ts", "yesterday-ish"),
        ("offset_ts", None),
    ):
        with connect(db) as conn:
            conn.execute(
                f"UPDATE fusion_day_volume SET {column} = ? WHERE symbol = 'TSLA'",
                (value,),
            )
            conn.commit()
        assert (
            get_day_volume(
                "TSLA",
                max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S,
                as_of=EVENT_TS,
                db_path=db,
            )
            is None
        )
        _seed_day_volume(db)  # restore for the next mutation


def test_day_volume_refuses_stale_and_lookahead_rows(tmp_path: Path) -> None:
    db = tmp_path / "v.db"
    init_fusion_tables(db)
    _seed_day_volume(db, as_of_ts="2026-07-17T14:00:00+00:00")  # 30 min behind
    assert (
        get_day_volume("TSLA", max_age_s=600.0, as_of=EVENT_TS, db_path=db) is None
    )
    # ...and the other side of the fence: a row from the FUTURE is lookahead.
    _seed_day_volume(db, as_of_ts="2026-07-17T15:00:00+00:00")
    assert (
        get_day_volume("TSLA", max_age_s=600.0, as_of=EVENT_TS, db_path=db) is None
    )


def test_day_volume_refuses_yesterdays_row(tmp_path: Path) -> None:
    # Yesterday's cumulative volume against today's time-of-day baseline would
    # read as a huge RVOL at the open — it must read as MISSING.
    db = tmp_path / "v.db"
    init_fusion_tables(db)
    _seed_day_volume(db, market_day="2026-07-16")
    assert (
        get_day_volume(
            "TSLA",
            max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S,
            as_of=EVENT_TS,
            db_path=db,
        )
        is None
    )


# ------------------------------------------------------------- producer job


def _et(day: str, hh: int, mm: int):
    from datetime import datetime

    from trading.market_hours import _ET

    return datetime(*(int(p) for p in day.split("-")), hh, mm, tzinfo=_ET)


class _FakeProducer:
    """The producer job wired to a fake Alpaca and an injectable clock."""

    def __init__(self, db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import db.base as db_base
        from api import research_jobs as rj
        from signals import universe as universe_mod

        self.rj = rj
        self.volume = 500_000
        self.symbols: list[str] = ["TSLA"]
        monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
        monkeypatch.setattr(rj, "profile_universe", lambda: list(self.symbols))
        monkeypatch.setattr(universe_mod, "open_episode_symbols", lambda: [])

        outer = self

        class _Provider:
            async def aget_day_volumes(self, symbols):
                return {s: (outer.volume, "") for s in symbols}

        def _provider(name=None):
            assert name == "alpaca"  # never the hybrid router (Schwab burst hazard)
            return _Provider()

        monkeypatch.setattr(rj, "get_market_data_provider", _provider)

    def run(self, at) -> int:
        import asyncio

        return asyncio.run(self.rj._fusion_day_volume_refresh_once(now=at))


def _row(db: Path, symbol: str = "TSLA") -> dict:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM fusion_day_volume WHERE symbol = ?", (symbol,)
        ).fetchone()
    return dict(row) if row is not None else {}


def test_producer_latches_the_premarket_offset_once_per_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day's FIRST in-window sample is the premarket offset; every later
    sample moves day_volume only. Without the latch, the offset would chase the
    counter and the RTH total would collapse to ~0."""
    db = tmp_path / "prod.db"
    init_fusion_tables(db)
    producer = _FakeProducer(db, monkeypatch)

    assert producer.run(_et(MARKET_DAY, 9, 31)) == 1
    producer.volume = 2_100_000
    assert producer.run(_et(MARKET_DAY, 10, 30)) == 1

    row = _row(db)
    assert row["premarket_offset"] == "500000"  # latched at the first sample
    assert row["day_volume"] == "2100000"
    assert row["market_day"] == MARKET_DAY

    got = get_day_volume(
        "TSLA",
        max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S,
        as_of=EVENT_TS,
        db_path=db,
    )
    assert got == (Decimal("1600000"), Decimal("2100000"), row["offset_ts"])


def test_producer_refuses_to_latch_an_offset_mid_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE fabricated-low bug: a symbol that enters the universe mid-session
    (a new episode on a name outside profile_universe) had its ENTIRE day so
    far latched as "premarket", so rth_volume ≈ 0 and the dot published ~0.0x
    as a real reading for the rest of the day — on exactly the fresh-burst
    population this lane exists to describe."""
    db = tmp_path / "prod.db"
    init_fusion_tables(db)
    producer = _FakeProducer(db, monkeypatch)
    producer.volume = 4_000_000  # a whole morning of volume, first seen at 11:00

    assert producer.run(_et(MARKET_DAY, 11, 0)) == 1
    row = _row(db)
    assert row["premarket_offset"] is None  # unknown, NOT zero and NOT 4,000,000
    assert row["offset_ts"] is None
    assert row["day_volume"] == "4000000"
    # ...and it stays unknown for the rest of the day, however many passes run.
    producer.volume = 5_200_000
    producer.run(_et(MARKET_DAY, 14, 0))
    assert _row(db)["premarket_offset"] is None
    assert (
        get_day_volume(
            "TSLA",
            max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S,
            as_of="2026-07-17T18:00:00+00:00",
            db_path=db,
        )
        is None
    )

    # Next session's open latches a real offset and the reading resumes.
    producer.volume = 90_000
    producer.run(_et("2026-07-20", 9, 32))
    producer.volume = 1_400_000
    producer.run(_et("2026-07-20", 10, 30))
    row = _row(db)
    assert row["premarket_offset"] == "90000"
    assert row["market_day"] == "2026-07-20"
    got = get_day_volume(
        "TSLA",
        max_age_s=FUSION_RVOL_DAY_VOLUME_MAX_AGE_S,
        as_of="2026-07-20T14:30:00+00:00",
        db_path=db,
    )
    assert got is not None and got[0] == Decimal("1310000")


def test_producer_unlatches_an_offset_the_day_volume_falls_below(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alpaca serves the PREVIOUS session's dailyBar until a symbol's first
    trade of the day. A thin name latches yesterday's total at 09:31; when
    today's bar starts the counter drops BELOW the offset. Re-latch while still
    inside the window, unlatch after it — never a clamped 0.0x."""
    db = tmp_path / "prod.db"
    init_fusion_tables(db)
    producer = _FakeProducer(db, monkeypatch)

    producer.volume = 3_000_000  # yesterday's daily bar — no trade yet today
    producer.run(_et(MARKET_DAY, 9, 31))
    assert _row(db)["premarket_offset"] == "3000000"

    producer.volume = 40_000  # today's bar finally starts
    producer.run(_et(MARKET_DAY, 9, 36))
    assert _row(db)["premarket_offset"] == "40000"  # re-latched, still in window

    # The same regression seen AFTER the window is not salvageable.
    producer.volume = 10_000
    producer.run(_et(MARKET_DAY, 11, 0))
    assert _row(db)["premarket_offset"] is None


# -------------------------------------------------------------- the rvol lane


def test_rvol_dot_mints_annotate_only_with_prints_subtracted(fusion_db: Path) -> None:
    _save_profile()
    _seed_day_volume(fusion_db)
    _seed_print(fusion_db)
    _seed_actor(fusion_db)

    _run_intake(fusion_db)

    ep = _episode_id(fusion_db)
    (dot,) = _rvol_dots(fusion_db, ep)
    assert dot["actor_group"] == "background_crowd"
    assert dot["dot_kind"] == "state"
    assert dot["direction"] == "none"
    assert dot["source_store"] == "volume_profile"
    assert dot["source_event_id"] == f"{ep}:rvol_ex_prints"
    # ANNOTATE-ONLY: always zero points, never in group_scores.
    assert float(dot["points"]) == 0.0
    assert "background_crowd" not in _group_scores(fusion_db, ep)
    # The reading lives in raw_value (lifecycle._latest_rvol reads it).
    assert float(dot["raw_value"]) == pytest.approx(1.80)

    detail = json.loads(dot["detail_json"])
    assert detail["state"] == "live"
    assert detail["rvol_ex_prints"] == pytest.approx(1.80)
    assert detail["rvol_raw"] == pytest.approx(2.00)
    assert detail["prints_shares_subtracted"] == PRINT_SHARES
    assert detail["day_volume"] == pytest.approx(float(DAY_VOLUME))
    assert detail["premarket_offset"] == pytest.approx(float(PREMARKET_OFFSET))
    assert detail["expected_volume"] == pytest.approx(EXPECTED_BY_NOW)
    assert detail["bucket"] == BUCKET
    assert detail["minutes_since_open"] == 60
    assert detail["profile_n_days"] == 20
    assert detail["volume_source"] == "alpaca_snapshot"
    assert detail["baseline_contains_prints"] is True


def test_rvol_reading_and_ts_advance_never_freeze(fusion_db: Path) -> None:
    """The _insert_dot_on freeze this round fixed: without refresh_state the
    rolling dot's raw_value AND ts stay pinned at the first reading, so
    lifecycle._latest_rvol (recency-selected) reads a stale multiple forever."""
    _save_profile()
    _seed_day_volume(fusion_db)
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    (first,) = _rvol_dots(fusion_db, ep)
    assert float(first["raw_value"]) == pytest.approx(2.00)

    # 30 minutes later, same episode, volume barely moved → RVOL falls.
    later_ts = "2026-07-17T15:00:00+00:00"  # 11:00 ET, bucket 18
    _clear_memos()
    _seed_day_volume(fusion_db, volume=3_400_000, as_of_ts=later_ts)
    insert_flow(_flow("flow-2", ts=later_ts), fusion_db)
    _run_intake(fusion_db)

    (second,) = _rvol_dots(fusion_db, ep)  # still ONE dot — rolling, not appended
    assert second["ts"] == later_ts
    # expected = 7.8M * 19/78 = 1.9M; (3.4M - 0.4M) / 1.9M = 1.58
    assert float(second["raw_value"]) == pytest.approx(1.58)


def test_rvol_publishes_none_not_a_frozen_reading_when_volume_goes_stale(
    fusion_db: Path,
) -> None:
    _save_profile()
    _seed_day_volume(fusion_db)
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    assert float(_rvol_dots(fusion_db, ep)[0]["raw_value"]) == pytest.approx(2.00)

    # The producer stops updating: the next pass's event is far from the row.
    later_ts = "2026-07-17T15:30:00+00:00"
    _clear_memos()
    insert_flow(_flow("flow-2", ts=later_ts), fusion_db)
    _run_intake(fusion_db)

    (dot,) = _rvol_dots(fusion_db, ep)
    assert dot["raw_value"] is None  # never the last good reading, never 0.0
    assert float(dot["points"]) == 0.0
    assert dot["ts"] == later_ts
    assert json.loads(dot["detail_json"])["state"] == "no_volume"


def test_no_profile_mints_nothing_on_a_first_pass(fusion_db: Path) -> None:
    # No baseline → no claim at all. An episode must not carry an empty
    # rvol claim it has never been able to fill.
    _seed_day_volume(fusion_db)
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    assert _rvol_dots(fusion_db, _episode_id(fusion_db)) == []


def test_warming_window_publishes_no_reading(fusion_db: Path) -> None:
    _save_profile()
    open_ts = "2026-07-17T13:35:00+00:00"  # 09:35 ET — 5 min in, under warmup
    _seed_day_volume(fusion_db, as_of_ts=open_ts)
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    insert_flow(_flow("flow-open", ts=open_ts), fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    assert _rvol_dots(fusion_db, ep) == []  # nothing to refresh yet

    # Once a reading exists, a later warming pass REFRESHES it to no-reading
    # rather than leaving yesterday's/earlier value standing.
    _clear_memos()
    _seed_day_volume(fusion_db)
    insert_flow(_flow("flow-mid"), fusion_db)
    _run_intake(fusion_db)
    assert float(_rvol_dots(fusion_db, ep)[0]["raw_value"]) == pytest.approx(2.00)

    _clear_memos()
    next_open = "2026-07-20T13:35:00+00:00"  # Monday 09:35 ET
    _seed_day_volume(fusion_db, market_day="2026-07-20", as_of_ts=next_open)
    insert_flow(_flow("flow-mon", ts=next_open), fusion_db)
    _run_intake(fusion_db)
    (dot,) = _rvol_dots(fusion_db, ep)
    assert dot["raw_value"] is None
    assert json.loads(dot["detail_json"])["state"] == "warming"


def test_five_minute_write_throttle_skips_the_same_bucket(fusion_db: Path) -> None:
    _save_profile()
    _seed_day_volume(fusion_db)
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    first_created = _rvol_dots(fusion_db, ep)[0]["created_at"]

    # A second event in the SAME 5-minute bucket must not re-write the dot
    # (the memo is deliberately NOT cleared here — that is the mechanism).
    _seed_day_volume(fusion_db, volume=9_000_000)
    insert_flow(_flow("flow-same-bucket", ts="2026-07-17T14:32:00+00:00"), fusion_db)
    _run_intake(fusion_db)
    (dot,) = _rvol_dots(fusion_db, ep)
    assert dot["created_at"] == first_created
    assert float(dot["raw_value"]) == pytest.approx(2.00)


def test_midday_arrival_publishes_nothing_all_day_then_reads_next_session(
    fusion_db: Path,
) -> None:
    """End of the same thread as the producer test, seen from the dot: with no
    latched offset the episode carries NO rvol claim at all that day — not a
    0.0x, not a frozen number — and picks the reading up at the next open."""
    _save_profile()
    _seed_day_volume(fusion_db, offset=None)  # first seen mid-session
    _seed_actor(fusion_db)
    _run_intake(fusion_db)
    ep = _episode_id(fusion_db)
    assert _rvol_dots(fusion_db, ep) == []

    _clear_memos()
    late_ts = "2026-07-17T19:30:00+00:00"  # 15:30 ET, same session
    _seed_day_volume(fusion_db, offset=None, volume=9_000_000, as_of_ts=late_ts)
    insert_flow(_flow("flow-late", ts=late_ts), fusion_db)
    _run_intake(fusion_db)
    assert _rvol_dots(fusion_db, ep) == []

    _clear_memos()
    monday = "2026-07-20T14:30:00+00:00"  # 10:30 ET
    _seed_day_volume(
        fusion_db,
        market_day="2026-07-20",
        as_of_ts=monday,
        offset_ts="2026-07-20T13:32:00+00:00",
    )
    insert_flow(_flow("flow-mon", ts=monday), fusion_db)
    _run_intake(fusion_db)
    (dot,) = _rvol_dots(fusion_db, ep)
    assert float(dot["raw_value"]) == pytest.approx(2.00)


def test_prints_before_the_offset_stamp_are_not_subtracted(fusion_db: Path) -> None:
    """The numerator is day_volume MINUS the offset, i.e. it counts from the
    latch instant — so a premarket block (the collector runs 4am-8pm) was never
    in it. Subtracting it anyway is pure over-subtraction, and a big enough
    premarket cross drove the numerator into the max(0.0, ...) clamp and
    published a fabricated 0.00x."""
    _save_profile()
    _seed_day_volume(fusion_db)
    _seed_actor(fusion_db)
    insert_tape(
        {
            "id": "tape-premarket",
            "ts": "2026-07-17T12:15:00+00:00",  # 08:15 ET — before the latch
            "symbol": "TSLA",
            "event_type": "dark_pool",
            "price": 320.0,
            "size": 2_900_000,  # bigger than the whole RTH total
            "notional_usd": 2_900_000 * 320.0,
            "direction": "buy_side",
            "created_at": "2026-07-17T12:15:00+00:00",
        },
        fusion_db,
    )
    _seed_print(fusion_db)  # 10:00 ET — inside the window, still subtracted

    _run_intake(fusion_db)
    (dot,) = _rvol_dots(fusion_db, _episode_id(fusion_db))
    detail = json.loads(dot["detail_json"])
    assert detail["prints_shares_subtracted"] == PRINT_SHARES
    assert float(dot["raw_value"]) == pytest.approx(1.80)


def test_latest_rvol_refuses_a_previous_session_reading() -> None:
    """lifecycle._latest_rvol feeds the rvol_confirm trigger. The dot only
    refreshes on a pass that TOUCHES the episode, so a quiet episode still
    carries yesterday's multiple with yesterday's ts — and RVOL is a
    today-vs-baseline statement. Confirming on it is a fabricated confirmation.
    """
    from datetime import datetime

    from signals.fusion.lifecycle import _latest_rvol
    from trading.market_hours import _ET

    now = datetime(2026, 7, 20, 10, 30, tzinfo=_ET)  # Monday
    friday = {
        "component": "rvol_ex_prints",
        "raw_value": "3.40",
        "ts": "2026-07-17T18:00:00+00:00",
    }
    today = {
        "component": "rvol_ex_prints",
        "raw_value": "1.10",
        "ts": "2026-07-20T14:00:00+00:00",
    }
    assert _latest_rvol([friday], now) is None
    assert _latest_rvol([friday, today], now) == pytest.approx(1.10)
    # A None reading (an unavailable state) is never a number either.
    assert _latest_rvol([{**today, "raw_value": None}], now) is None
