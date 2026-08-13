from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from db.base import connect
from db.equity_tape import init_db as init_tape_db
from db.equity_tape import insert_event as insert_tape
from db.fusion import conservation_counts, init_fusion_tables, put_atr
from db.options_flow import init_db as init_flow_db
from db.options_flow import insert_event as insert_flow
from signals.fusion.intake import intake_once


@pytest.fixture
def fusion_db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    return path


def _flow(event_id: str, premium: int = 1_000_000) -> dict:
    return {
        "id": event_id,
        "ts": "2026-07-17T14:30:00+00:00",
        "underlying": "TSLA",
        "occ_symbol": "TSLA260821C00300000",
        "right": "call",
        "event_type": "sweep",
        "premium_usd": premium,
        "direction": "buy_side",
        "created_at": "2026-07-17T14:30:01+00:00",
    }


def _tape(event_id: str, notional: int = 2_000_000) -> dict:
    return {
        "id": event_id,
        "ts": "2026-07-17T14:40:00+00:00",
        "symbol": "TSLA",
        "event_type": "dark_pool",
        "price": 320,
        "size": 6250,
        "notional_usd": notional,
        "direction": "buy_side",
        "created_at": "2026-07-17T14:40:01+00:00",
    }


def test_ingest_seq_is_assigned_once_and_preserved_on_upsert(fusion_db: Path) -> None:
    insert_flow(_flow("flow-1"), fusion_db)
    insert_tape(_tape("tape-1"), fusion_db)
    with connect(fusion_db) as conn:
        flow_seq = conn.execute(
            "SELECT ingest_seq FROM options_flow_events WHERE id = 'flow-1'"
        ).fetchone()[0]
        tape_seq = conn.execute(
            "SELECT ingest_seq FROM equity_tape_events WHERE id = 'tape-1'"
        ).fetchone()[0]

    insert_flow(_flow("flow-1", premium=3_000_000), fusion_db)
    insert_tape(_tape("tape-1", notional=5_000_000), fusion_db)
    with connect(fusion_db) as conn:
        flow = conn.execute(
            "SELECT ingest_seq, premium_usd FROM options_flow_events WHERE id = 'flow-1'"
        ).fetchone()
        tape = conn.execute(
            "SELECT ingest_seq, notional_usd FROM equity_tape_events WHERE id = 'tape-1'"
        ).fetchone()
    assert flow["ingest_seq"] == flow_seq
    assert tape["ingest_seq"] == tape_seq
    assert flow["premium_usd"] == 3_000_000
    assert tape["notional_usd"] == 5_000_000


def test_ingest_seq_migration_backfills_created_order(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    with connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE options_flow_events (
                id TEXT PRIMARY KEY, ts TEXT NOT NULL, underlying TEXT NOT NULL,
                occ_symbol TEXT NOT NULL, right TEXT, strike REAL, expiry TEXT,
                dte INTEGER, otm_pct REAL, event_type TEXT NOT NULL,
                contracts INTEGER, premium_usd REAL, vol_oi_ratio REAL,
                vol_zscore REAL, direction TEXT, direction_basis TEXT,
                spread_flag INTEGER, n_prints INTEGER, exchanges TEXT,
                reason TEXT, caveat TEXT, ret_1d REAL, ret_5d REAL,
                created_at TEXT NOT NULL
            );
            INSERT INTO options_flow_events(
                id, ts, underlying, occ_symbol, event_type, created_at
            ) VALUES
                ('later', '2026-07-17T15:00:00Z', 'TSLA', 'O2', 'sweep',
                 '2026-07-17T15:00:01Z'),
                ('earlier', '2026-07-17T14:00:00Z', 'TSLA', 'O1', 'sweep',
                 '2026-07-17T14:00:01Z');

            CREATE TABLE equity_tape_events (
                id TEXT PRIMARY KEY, ts TEXT NOT NULL, symbol TEXT NOT NULL,
                event_type TEXT NOT NULL, exchange TEXT, price REAL, size INTEGER,
                notional_usd REAL, direction TEXT, direction_basis TEXT,
                conditions TEXT, reason TEXT, caveat TEXT, detail TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    init_flow_db(db)
    init_tape_db(db)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT id, ingest_seq FROM options_flow_events ORDER BY ingest_seq"
        ).fetchall()
    assert [(row["id"], row["ingest_seq"]) for row in rows] == [
        ("earlier", 1),
        ("later", 2),
    ]


def test_cursor_resume_has_no_gap_or_replay(
    fusion_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[dict, list[dict], dict]] = []
    monkeypatch.setattr(
        "signals.fusion.registrar.register_stack",
        lambda episode, dots, score, **kwargs: calls.append((episode, dots, score)),
    )
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    insert_flow(_flow("flow-1"), fusion_db)
    first = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320.25"))
    assert first["scored"] == 1

    second = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320.25"))
    assert sum(second.values()) == 0

    insert_tape(_tape("tape-1"), fusion_db)
    third = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("321.00"))
    assert third["scored"] == 1
    with connect(fusion_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_dispositions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM evidence_dots").fetchone()[0] == 2
    assert len(calls) == 1
    anchor, dots, score = calls[0]
    assert anchor["anchor_price"] == "320.25"
    assert anchor["anchor_atr14"] == "12.50"
    assert anchor["anchor_direction"] == "bull"
    assert dots[0]["component"] == "option_sweep"
    assert score["signature"] == "option_sweep"


def test_option_dot_carries_dte_in_detail(
    fusion_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The minted option dot persists the source event's DTE into detail_json so
    the v2 scorer routes its role (thesis vs trigger) live — evidence_dots have
    no DTE column, and scorer._dte reads it from detail."""
    monkeypatch.setattr(
        "signals.fusion.registrar.register_stack",
        lambda *args, **kwargs: None,
    )
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    event = _flow("flow-dte")
    event["dte"] = 30
    insert_flow(event, fusion_db)
    counters = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320.25"))
    assert counters["scored"] == 1
    with connect(fusion_db) as conn:
        detail_json = conn.execute(
            "SELECT detail_json FROM evidence_dots WHERE source_event_id = 'flow-dte'"
        ).fetchone()[0]
    assert json.loads(detail_json)["dte"] == 30


def test_unstampable_is_stored_and_conservation_balances(fusion_db: Path) -> None:
    insert_flow(_flow("flow-unstamped"), fusion_db)
    counters = intake_once(db_path=fusion_db)
    assert counters["unstampable"] == 1
    with connect(fusion_db) as conn:
        dot = conn.execute(
            "SELECT stamped FROM evidence_dots WHERE source_event_id = 'flow-unstamped'"
        ).fetchone()
        episode_score = conn.execute("SELECT score FROM fusion_episodes").fetchone()[0]
        disposition = conn.execute(
            """
            SELECT disposition FROM fusion_dispositions
            WHERE source_event_id = 'flow-unstamped'
            """
        ).fetchone()
    assert dot["stamped"] == 0
    assert episode_score == 0
    assert disposition["disposition"] == "unstampable"
    assert conservation_counts(db_path=fusion_db) == {"inputs": 1, "dispositions": 1}


def test_tape_dot_stamps_from_print_price_when_cache_misses(fusion_db: Path) -> None:
    """The ASTS fix: an off-cache tape name stamps from the print's own
    execution price (labeled price_source='print') instead of dying
    unstampable — with ATR present the dot is scored and carries mass."""
    insert_tape(_tape("tape-offcache"), fusion_db)
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    counters = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: None)
    assert counters["scored"] == 1
    assert counters["unstampable"] == 0
    with connect(fusion_db) as conn:
        dot = conn.execute(
            """SELECT stamped, trigger_price, detail_json FROM evidence_dots
               WHERE source_event_id = 'tape-offcache'"""
        ).fetchone()
    assert dot["stamped"] == 1
    assert Decimal(dot["trigger_price"]) == Decimal("320")
    assert json.loads(dot["detail_json"])["price_source"] == "print"


def test_options_dot_stays_unstampable_without_cached_price(fusion_db: Path) -> None:
    """No fallback for options events. The boundary is enforced by the schema
    (options_flow_events has no price column, so there is nothing to fall back
    to) with the store guard as belt-and-braces against a future price-ish
    column; this pins the observable outcome — unstampable, and no
    price_source label ever written on the flow dot."""
    insert_flow(_flow("flow-nocache"), fusion_db)
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    counters = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: None)
    assert counters["unstampable"] == 1
    with connect(fusion_db) as conn:
        dot = conn.execute(
            """SELECT stamped, detail_json FROM evidence_dots
               WHERE source_event_id = 'flow-nocache'"""
        ).fetchone()
    assert dot["stamped"] == 0
    assert "price_source" not in json.loads(dot["detail_json"])


def test_storm_shedding_accounts_every_event(fusion_db: Path) -> None:
    put_atr("TSLA", "2026-07-16", Decimal("10"), db_path=fusion_db)
    for number in range(4):
        insert_flow(_flow(f"flow-{number}", premium=(number + 1) * 100_000), fusion_db)
    counters = intake_once(
        db_path=fusion_db,
        price_lookup=lambda _symbol: Decimal("300"),
        storm_threshold=3,
        storm_keep=2,
    )
    assert counters["shed"] == 2
    assert conservation_counts(db_path=fusion_db) == {"inputs": 4, "dispositions": 4}


def test_unstampable_first_pass_never_locks_out_the_day(fusion_db: Path) -> None:
    # First event lands before the ATR cache warms: unstampable, and the
    # episode must NOT latch registration_called (the F2 lockout class).
    insert_flow(_flow("flow-early"), fusion_db)
    first = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320"))
    assert first["unstampable"] == 1
    with connect(fusion_db) as conn:
        latch = conn.execute(
            "SELECT registration_called FROM fusion_episodes"
        ).fetchone()[0]
    assert latch == 0

    # Caches warm, a second stampable print arrives: the day registers off it.
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    insert_flow(_flow("flow-late", premium=2_000_000), fusion_db)
    second = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("321"))
    assert second["scored"] == 1
    with connect(fusion_db) as conn:
        row = conn.execute(
            "SELECT registration_called, anchor_price FROM fusion_episodes"
        ).fetchone()
    assert row["registration_called"] == 1
    assert row["anchor_price"] == "321"


def test_registrar_crash_leaves_latch_open_and_replay_retries(
    fusion_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _boom(episode, dots, score, **kwargs):
        calls.append(episode["id"])
        if len(calls) == 1:
            raise RuntimeError("simulated crash mid-register")

    monkeypatch.setattr("signals.fusion.registrar.register_stack", _boom)
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    insert_flow(_flow("flow-1"), fusion_db)
    with pytest.raises(RuntimeError):
        intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320.25"))
    with connect(fusion_db) as conn:
        latch = conn.execute(
            "SELECT registration_called FROM fusion_episodes"
        ).fetchone()[0]
        cursor = conn.execute(
            "SELECT last_ingest_seq FROM bus_cursors WHERE store = 'options_flow'"
        ).fetchone()
    assert latch == 0  # the old latch-first order left 1 here: a silent hole
    assert cursor is None or cursor[0] == 0  # event not consumed — replay due

    second = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320.25"))
    assert second["scored"] == 1
    assert len(calls) == 2
    with connect(fusion_db) as conn:
        assert conn.execute(
            "SELECT registration_called FROM fusion_episodes"
        ).fetchone()[0] == 1


def test_market_day_naive_timestamp_is_utc() -> None:
    from signals.fusion.intake import _market_day

    # 02:30 naive = UTC = 22:30 ET the previous day. Attaching ET (the old
    # behavior) split one session's evidence across two episodes.
    assert _market_day("2026-07-18T02:30:00") == "2026-07-17"
    assert _market_day("2026-07-18T02:30:00Z") == "2026-07-17"
    assert _market_day("2026-07-17T14:30:00-04:00") == "2026-07-17"


def test_ensure_episode_keys_on_open_episode_per_symbol(
    fusion_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One OPEN episode per symbol is the new dedup key: two dots on different
    market days flow into the SAME open episode (spans days), and the partial
    unique index rejects a hand-inserted second open row."""
    import sqlite3

    from signals.fusion.intake import _ensure_episode

    first = _ensure_episode("TSLA", "2026-07-17", "2026-07-17T14:30:00+00:00", False, fusion_db)
    second = _ensure_episode("TSLA", "2026-07-20", "2026-07-20T14:30:00+00:00", False, fusion_db)
    assert first["id"] == second["id"]
    assert second["market_day"] == "2026-07-17"  # opening session preserved across days
    with connect(fusion_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM fusion_episodes WHERE symbol='TSLA'"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, "
                "updated_at, state) VALUES ('dup','TSLA','2026-07-21','t','t','open')"
            )
            conn.commit()
    assert count == 1


def test_shock_flag_clears_when_window_expires(
    fusion_db: Path,
) -> None:
    """Round B: market_shock is the current window, not a MAX() latch. Once
    shock_lookup goes false, the open episode must drop the flag — otherwise a
    10am print stamps the card for the rest of the episode's life."""
    from signals.fusion.intake import _ensure_episode

    ts = "2026-07-17T14:30:00+00:00"
    shocked = _ensure_episode("TSLA", "2026-07-17", ts, True, fusion_db)
    assert int(shocked["market_shock"]) == 1
    cleared = _ensure_episode("TSLA", "2026-07-17", ts, False, fusion_db)
    assert cleared["id"] == shocked["id"]
    assert int(cleared["market_shock"]) == 0
    rearmed = _ensure_episode("TSLA", "2026-07-17", ts, True, fusion_db)
    assert int(rearmed["market_shock"]) == 1


def test_rescore_persists_masses_and_last_evidence(fusion_db: Path) -> None:
    """_rescore stamps the v2 decayed thesis mass summary + last_evidence_at
    alongside the legacy undecayed score."""
    from datetime import datetime, timezone

    # A fresh-ts bull option print so the thesis mass is materially non-zero at
    # the wall-clock instant _rescore evaluates score_decayed. The ATR stamp
    # must track the event's day — get_atr refuses rows stale relative to the
    # event's session (FUSION_ATR_MAX_AGE_DAYS).
    from zoneinfo import ZoneInfo

    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    # ET day, not UTC day: get_atr's staleness/lookahead bounds anchor on the
    # event's ET session, and the UTC date runs a day ahead each evening.
    et_day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    put_atr("TSLA", et_day, Decimal("12.50"), db_path=fusion_db)
    event = _flow("flow-mass")
    event["ts"] = ts
    event["dte"] = 30  # thesis-class routing
    insert_flow(event, fusion_db)
    counters = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320.25"))
    assert counters["scored"] == 1
    with connect(fusion_db) as conn:
        row = conn.execute(
            "SELECT bull_mass, bear_mass, conviction, last_evidence_at "
            "FROM fusion_episodes WHERE symbol='TSLA'"
        ).fetchone()
    assert row["bull_mass"] > 0
    assert row["bear_mass"] == 0
    assert row["conviction"] > 0
    assert row["last_evidence_at"] == ts


def test_crash_replay_restamps_dot_and_registers(
    fusion_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The exact F2 chain: pass 1 runs before the caches warm AND dies before
    # the cursor advances (set_cursor no-op). The persisted dot is unstamped.
    # The replay has price+ATR: the upsert must fill the stamps so the day
    # anchors on real values instead of latching NULLs or staying locked out.
    insert_flow(_flow("flow-1"), fusion_db)
    monkeypatch.setattr("db.fusion.set_cursor", lambda *a, **k: None)
    first = intake_once(db_path=fusion_db)  # no price_lookup: unstampable
    assert first["unstampable"] == 1
    monkeypatch.undo()

    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    second = intake_once(db_path=fusion_db, price_lookup=lambda _symbol: Decimal("320.25"))
    assert second["scored"] == 1
    with connect(fusion_db) as conn:
        dot = conn.execute(
            "SELECT stamped, trigger_price FROM evidence_dots"
        ).fetchone()
        episode = conn.execute(
            "SELECT registration_called, anchor_price, anchor_atr14 FROM fusion_episodes"
        ).fetchone()
    assert dot["stamped"] == 1
    assert dot["trigger_price"] == "320.25"
    assert episode["registration_called"] == 1
    assert episode["anchor_price"] == "320.25"
    assert episode["anchor_atr14"] == "12.50"


def test_symbol_day_cadence_registers_each_market_day(fusion_db: Path) -> None:
    """2026-07-28 restore: Round-3 long-lived episodes had silently turned the
    pinned one-registration-per-SYMBOL-DAY into once per episode LIFETIME. The
    per-day latch registers the same open episode again on its next market day
    — anchored on THAT day's first stampable directional dot — while a second
    same-day dot still registers nothing."""
    put_atr("TSLA", "2026-07-17", Decimal("12.50"), db_path=fusion_db)
    insert_flow(_flow("flow-day1"), fusion_db)
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("320.25"))

    second_same_day = _flow("flow-day1b")
    second_same_day["ts"] = "2026-07-17T15:30:00+00:00"
    second_same_day["created_at"] = "2026-07-17T15:30:01+00:00"
    insert_flow(second_same_day, fusion_db)
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("321.00"))

    def _mech_rows() -> list[dict]:
        with connect(fusion_db) as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM idea_outcomes WHERE origin='fusion_mech' "
                    "ORDER BY as_of"
                ).fetchall()
            ]

    assert len(_mech_rows()) == 1  # same day = one registration

    # Next trading day (Mon 07-20): the SAME open episode registers again.
    put_atr("TSLA", "2026-07-20", Decimal("12.50"), db_path=fusion_db)
    day2 = _flow("flow-day2")
    day2["ts"] = "2026-07-20T14:30:00+00:00"
    day2["created_at"] = "2026-07-20T14:30:01+00:00"
    insert_flow(day2, fusion_db)
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("330.50"))

    rows = _mech_rows()
    assert len(rows) == 2
    assert rows[1]["as_of"] == "2026-07-20T14:30:00+00:00"
    # Day-2's plan anchors on day-2's dot price, not the episode's day-1 anchor.
    assert rows[1]["entry_low"] == "330.50"
    with connect(fusion_db) as conn:
        ep = conn.execute(
            "SELECT anchor_price, registered_market_day, COUNT(*) OVER () AS n "
            "FROM fusion_episodes"
        ).fetchone()
    assert ep["n"] == 1  # one open episode carried both days
    assert ep["anchor_price"] == "320.25"  # episode anchor stays first-ever
    assert ep["registered_market_day"] == "2026-07-20"


def test_spot_cache_freshness_bound(fusion_db: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from db.fusion import get_spot, put_spots

    fresh_ts = (datetime.now(UTC) - timedelta(seconds=100)).isoformat()
    stale_ts = (datetime.now(UTC) - timedelta(seconds=2000)).isoformat()
    put_spots(
        [("ASTS", "12.34", fresh_ts), ("OLDY", "9.99", stale_ts)], db_path=fusion_db
    )
    assert get_spot("ASTS", max_age_s=900, db_path=fusion_db) == Decimal("12.34")
    assert get_spot("OLDY", max_age_s=900, db_path=fusion_db) is None  # stale
    assert get_spot("OLDY", max_age_s=3000, db_path=fusion_db) == Decimal("9.99")
    assert get_spot("MISSING", max_age_s=900, db_path=fusion_db) is None


def test_options_event_prices_from_spot_cache_when_bars_cache_misses(
    fusion_db: Path,
) -> None:
    """Whole-market fix (2026-07-28): an options event on a non-watchlist
    underlying — price_lookup misses — stamps from fusion_spot_cache instead of
    minting an unstamped dot, labeled for the honesty trail."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from db.fusion import put_spots

    now = datetime.now(UTC)
    et_day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    put_atr("ASTS", et_day, Decimal("0.80"), db_path=fusion_db)
    put_spots([("ASTS", "12.34", now.isoformat())], db_path=fusion_db)
    event = _flow("flow-spot")
    event["underlying"] = "ASTS"
    event["occ_symbol"] = "ASTS260821C00015000"
    event["ts"] = now.replace(microsecond=0).isoformat()
    event["created_at"] = now.replace(microsecond=0).isoformat()
    insert_flow(event, fusion_db)
    counters = intake_once(db_path=fusion_db)  # no price_lookup at all
    assert counters["scored"] == 1
    with connect(fusion_db) as conn:
        dot = conn.execute(
            "SELECT stamped, trigger_price, detail_json FROM evidence_dots "
            "WHERE source_event_id = 'flow-spot'"
        ).fetchone()
        idea = conn.execute(
            "SELECT entry_low FROM idea_outcomes WHERE origin='fusion_mech'"
        ).fetchone()
    assert dot["stamped"] == 1
    assert dot["trigger_price"] == "12.34"
    assert json.loads(dot["detail_json"])["price_source"] == "tape_spot"
    assert idea["entry_low"] == "12.34"  # the plan anchors on the spot


def test_lagging_older_day_event_never_registers(fusion_db: Path) -> None:
    """Review H1/H2: the latch is MONOTONIC. A lagging older-day event (store
    cursor catch-up, multi-session outage) must neither register that past
    session at a fresh price nor walk the latch backward and unlock the
    current day for a duplicate."""
    put_atr("TSLA", "2026-07-17", Decimal("12.50"), db_path=fusion_db)
    insert_flow(_flow("flow-mon-day1"), fusion_db)  # ts 2026-07-17
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("320.25"))
    put_atr("TSLA", "2026-07-20", Decimal("12.50"), db_path=fusion_db)
    day2 = _flow("flow-mon-day2")
    day2["ts"] = "2026-07-20T14:30:00+00:00"
    day2["created_at"] = "2026-07-20T14:30:01+00:00"
    insert_flow(day2, fusion_db)
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("330.50"))

    # The laggard: a NEW event whose ts belongs to the already-registered
    # 07-17 session arrives after 07-20 registered.
    put_atr("TSLA", "2026-07-17", Decimal("12.50"), db_path=fusion_db)
    laggard = _flow("flow-mon-laggard")
    laggard["ts"] = "2026-07-17T18:00:00+00:00"
    laggard["created_at"] = "2026-07-20T15:00:01+00:00"
    insert_flow(laggard, fusion_db)
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("999.99"))

    with connect(fusion_db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM idea_outcomes WHERE origin='fusion_mech'"
        ).fetchone()[0]
        latch = conn.execute(
            "SELECT registered_market_day FROM fusion_episodes"
        ).fetchone()[0]
    assert n == 2  # day1 + day2 only — the laggard registered nothing
    assert latch == "2026-07-20"  # and the latch never walked backward
