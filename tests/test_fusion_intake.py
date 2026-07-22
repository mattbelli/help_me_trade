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


def test_rescore_persists_masses_and_last_evidence(fusion_db: Path) -> None:
    """_rescore stamps the v2 decayed thesis mass summary + last_evidence_at
    alongside the legacy undecayed score."""
    from datetime import datetime, timezone

    # A fresh-ts bull option print so the thesis mass is materially non-zero at
    # the wall-clock instant _rescore evaluates score_decayed.
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
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
