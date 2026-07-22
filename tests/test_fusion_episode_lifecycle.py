"""Episode lifecycle sweep + schema migration (Fusion Horizon Redesign §2.4).

Every case is driven by synthetic dots, an injected price_lookup, and a fixed
``now`` — no live market data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from db.base import connect
from db.fusion import init_fusion_tables
from signals.fusion.intake import _ensure_episode, _rescore
from signals.fusion.lifecycle import sweep_lifecycle

_UTC = timezone.utc


@pytest.fixture
def fdb(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    return path


def _ep(
    db: Path,
    episode_id: str,
    symbol: str,
    opened: str,
    *,
    state: str = "open",
    anchor_price: str | None = None,
    anchor_atr14: str | None = None,
    anchor_direction: str | None = None,
    bull_mass: float = 0.0,
    bear_mass: float = 0.0,
    last_evidence_at: str | None = None,
    close_reason: str | None = None,
) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, state,
                anchor_price, anchor_atr14, anchor_direction, bull_mass, bear_mass,
                last_evidence_at, close_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                symbol,
                opened[:10],
                opened,
                opened,
                state,
                anchor_price,
                anchor_atr14,
                anchor_direction,
                bull_mass,
                bear_mass,
                last_evidence_at,
                close_reason,
            ),
        )
        conn.commit()


def _dot(
    db: Path,
    dot_id: str,
    episode_id: str,
    symbol: str,
    ts: str,
    *,
    direction: str = "bull",
    raw_value: str = "1000000",
    trigger_price: str | None = None,
) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO evidence_dots(
                id, episode_id, source_store, source_event_id, source_ingest_seq,
                symbol, ts, component, actor_group, dot_kind, direction, raw_value,
                points, detail_json, trigger_price, stamped, created_at
            ) VALUES (
                ?, ?, 'options_flow', ?, 1, ?, ?, 'option_block', 'options_actor',
                'event', ?, ?, 0, '{"dte":30}', ?, 1, ?
            )
            """,
            (dot_id, episode_id, f"ev-{dot_id}", symbol, ts, direction, raw_value, trigger_price, ts),
        )
        conn.commit()


def _state(db: Path, episode_id: str) -> dict:
    with connect(db) as conn:
        return dict(
            conn.execute(
                "SELECT state, close_reason FROM fusion_episodes WHERE id = ?",
                (episode_id,),
            ).fetchone()
        )


# ------------------------------------------------------------------- close rules
def test_price_invalidation_bull_and_bear(fdb: Path) -> None:
    # bull anchor 100, ATR 5, K=1.5 → close when price ≤ 92.5.
    _ep(fdb, "EB", "AAA", "2026-07-20T14:00:00+00:00", anchor_price="100",
        anchor_atr14="5", anchor_direction="bull")
    _dot(fdb, "d1", "EB", "AAA", "2026-07-20T14:00:00+00:00", trigger_price="100")
    now = datetime(2026, 7, 20, 17, 0, tzinfo=_UTC)
    counts = sweep_lifecycle(now, fdb, price_lookup=lambda _s: Decimal("92.0"))
    assert counts["episodes_closed"] == 1
    assert _state(fdb, "EB") == {"state": "closed", "close_reason": "price_invalidation"}

    # A price above the band does NOT close.
    _ep(fdb, "EB2", "AA2", "2026-07-20T14:00:00+00:00", anchor_price="100",
        anchor_atr14="5", anchor_direction="bull")
    _dot(fdb, "d2", "EB2", "AA2", "2026-07-20T14:00:00+00:00", trigger_price="100")
    sweep_lifecycle(now, fdb, price_lookup=lambda _s: Decimal("96.0"))
    assert _state(fdb, "EB2")["state"] == "open"

    # bear anchor 100, ATR 5 → close when price ≥ 107.5.
    _ep(fdb, "ES", "BBB", "2026-07-20T14:00:00+00:00", anchor_price="100",
        anchor_atr14="5", anchor_direction="bear")
    _dot(fdb, "d3", "ES", "BBB", "2026-07-20T14:00:00+00:00", direction="bear", trigger_price="100")
    sweep_lifecycle(now, fdb, price_lookup=lambda _s: Decimal("108.0"))
    assert _state(fdb, "ES") == {"state": "closed", "close_reason": "price_invalidation"}


def test_decay_to_noise_closes_after_thesis_mass_fades(fdb: Path) -> None:
    # A material bull thesis (point 2.0) opened 07-13; fresh, it holds the episode
    # open, and once its mass decays below the floor across N_DECAY sessions it
    # closes decay_to_noise (no anchor/price → the other rules are inert).
    _ep(fdb, "E", "CCC", "2026-07-13T14:00:00+00:00")
    _dot(fdb, "d1", "E", "CCC", "2026-07-13T14:00:00+00:00", raw_value="1000000")

    fresh = sweep_lifecycle(datetime(2026, 7, 13, 15, 0, tzinfo=_UTC), fdb)
    assert fresh["episodes_closed"] == 0
    assert _state(fdb, "E")["state"] == "open"  # fresh mass holds it open

    faded = sweep_lifecycle(datetime(2026, 7, 23, 20, 0, tzinfo=_UTC), fdb)
    assert faded["episodes_closed"] == 1
    assert _state(fdb, "E") == {"state": "closed", "close_reason": "decay_to_noise"}


def test_hard_ttl_closes_after_ttl_sessions(fdb: Path, monkeypatch) -> None:
    # Floor pinned to 0 so decay_to_noise can't pre-empt; ≥15 trading days after
    # open with no anchor → hard_ttl.
    monkeypatch.setattr("signals.fusion.lifecycle.FUSION_EPISODE_DECAY_FLOOR", 0.0)
    _ep(fdb, "E", "DDD", "2026-06-01T14:00:00+00:00")
    _dot(fdb, "d1", "E", "DDD", "2026-06-01T14:00:00+00:00")
    counts = sweep_lifecycle(datetime(2026, 7, 20, 20, 0, tzinfo=_UTC), fdb)
    assert counts["episodes_closed"] == 1
    assert _state(fdb, "E") == {"state": "closed", "close_reason": "hard_ttl"}


def test_price_invalidation_precedes_hard_ttl(fdb: Path, monkeypatch) -> None:
    # Both hard_ttl and price_invalidation would fire; the ordering stamps
    # price_invalidation first.
    monkeypatch.setattr("signals.fusion.lifecycle.FUSION_EPISODE_DECAY_FLOOR", 0.0)
    _ep(fdb, "E", "EEE", "2026-06-01T14:00:00+00:00", anchor_price="100",
        anchor_atr14="5", anchor_direction="bull")
    _dot(fdb, "d1", "E", "EEE", "2026-06-01T14:00:00+00:00", trigger_price="100")
    sweep_lifecycle(datetime(2026, 7, 20, 20, 0, tzinfo=_UTC), fdb,
                    price_lookup=lambda _s: Decimal("80.0"))
    assert _state(fdb, "E")["close_reason"] == "price_invalidation"


# --------------------------------------------------------------- sequential opens
def test_sequential_episode_opens_after_close(fdb: Path) -> None:
    first = _ensure_episode("AAA", "2026-07-20", "2026-07-20T14:00:00+00:00", False, fdb)
    with connect(fdb) as conn:
        conn.execute(
            "UPDATE fusion_episodes SET state='closed', close_reason='hard_ttl' WHERE id=?",
            (first["id"],),
        )
        conn.commit()
    # A genuinely new post-close dot opens the NEXT episode, never reopens the closed one.
    second = _ensure_episode("AAA", "2026-07-25", "2026-07-25T15:00:00+00:00", False, fdb)
    assert second["id"] != first["id"]
    assert second["state"] == "open"
    with connect(fdb) as conn:
        states = dict(
            conn.execute(
                "SELECT id, state FROM fusion_episodes WHERE symbol='AAA'"
            ).fetchall()
        )
    assert states == {first["id"]: "closed", second["id"]: "open"}


def test_ensure_episode_reuses_the_single_open_episode(fdb: Path) -> None:
    a = _ensure_episode("ZZZ", "2026-07-20", "2026-07-20T14:00:00+00:00", False, fdb)
    # A later dot on a NEW market day reuses the SAME open episode (spans days).
    b = _ensure_episode("ZZZ", "2026-07-21", "2026-07-21T14:00:00+00:00", False, fdb)
    assert a["id"] == b["id"]
    assert b["market_day"] == "2026-07-20"  # opening session preserved
    with connect(fdb) as conn:
        n = conn.execute("SELECT COUNT(*) FROM fusion_episodes WHERE symbol='ZZZ'").fetchone()[0]
    assert n == 1


def test_partial_unique_index_rejects_a_second_open(fdb: Path) -> None:
    import sqlite3

    _ensure_episode("QQQ", "2026-07-20", "2026-07-20T14:00:00+00:00", False, fdb)
    with connect(fdb) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at, state) "
            "VALUES ('other', 'QQQ', '2026-07-21', 't', 't', 'open')"
        )
        conn.commit()


# ------------------------------------------------------------- cold-storage seed
def test_rehydration_seeds_decayed_prior_mass_within_window(fdb: Path) -> None:
    _ep(fdb, "OLD", "AAA", "2026-07-06T14:00:00+00:00", state="closed",
        bull_mass=4.0, last_evidence_at="2026-07-06T14:00:00+00:00", close_reason="hard_ttl")
    # Returns 2 trading days later → within the 10-td window → seeded, decayed.
    new = _ensure_episode("AAA", "2026-07-08", "2026-07-08T14:00:00+00:00", False, fdb)
    assert new["id"] != "OLD"
    assert 0.0 < new["bull_mass"] < 4.0  # attenuated prior mass, not zero
    assert new["bear_mass"] == 0.0


def test_rehydration_seeds_empty_beyond_window(fdb: Path) -> None:
    _ep(fdb, "OLD", "AAA", "2026-07-06T14:00:00+00:00", state="closed",
        bull_mass=4.0, last_evidence_at="2026-07-06T14:00:00+00:00", close_reason="hard_ttl")
    # Returns well beyond the 10-td window → seed empty.
    new = _ensure_episode("AAA", "2026-08-15", "2026-08-15T14:00:00+00:00", False, fdb)
    assert new["bull_mass"] == 0.0
    assert new["bear_mass"] == 0.0


def test_rehydration_seed_survives_rescore(fdb: Path, monkeypatch) -> None:
    """Review finding: the seed used to live only in bull/bear_mass, so the first
    _rescore (own-dots recompute) erased it and §2.4 was a dead feature. The
    folded summary must exceed what the episode's own dots alone produce."""
    monkeypatch.setattr("signals.fusion.intake.now_iso", lambda: "2026-07-08T15:00:00+00:00")
    _ep(fdb, "OLD", "AAA", "2026-07-06T14:00:00+00:00", state="closed",
        bull_mass=4.0, last_evidence_at="2026-07-06T14:00:00+00:00", close_reason="hard_ttl")
    seeded = _ensure_episode("AAA", "2026-07-08", "2026-07-08T14:00:00+00:00", False, fdb)
    assert seeded["seed_bull_mass"] > 0.0
    # Twin symbol with NO cold storage = the own-dots-only control.
    control = _ensure_episode("BBB", "2026-07-08", "2026-07-08T14:00:00+00:00", False, fdb)
    _dot(fdb, "da", seeded["id"], "AAA", "2026-07-08T14:30:00+00:00")
    _dot(fdb, "db", control["id"], "BBB", "2026-07-08T14:30:00+00:00")
    _rescore(seeded["id"], fdb)
    _rescore(control["id"], fdb)
    with connect(fdb) as conn:
        rows = {
            r["symbol"]: dict(r)
            for r in conn.execute("SELECT symbol, bull_mass FROM fusion_episodes")
        }
    assert rows["BBB"]["bull_mass"] > 0.0  # the dot itself scored
    assert rows["AAA"]["bull_mass"] > rows["BBB"]["bull_mass"]  # seed folded in


def test_rescore_never_mutates_closed_episode(fdb: Path) -> None:
    """A closed episode is immutable cold storage — the replay path hands
    _rescore a closed row on purpose, and the sweep can close one mid-intake
    (concurrent threads). The guarded UPDATE must be a no-op."""
    _ep(fdb, "E", "AAA", "2026-07-06T14:00:00+00:00", state="closed",
        bull_mass=1.5, last_evidence_at="2026-07-06T14:00:00+00:00", close_reason="hard_ttl")
    _dot(fdb, "d1", "E", "AAA", "2026-07-08T14:30:00+00:00")
    _rescore("E", fdb)
    with connect(fdb) as conn:
        row = dict(conn.execute(
            "SELECT bull_mass, last_evidence_at, updated_at FROM fusion_episodes WHERE id='E'"
        ).fetchone())
    assert row["bull_mass"] == 1.5
    assert row["last_evidence_at"] == "2026-07-06T14:00:00+00:00"
    assert row["updated_at"] == "2026-07-06T14:00:00+00:00"


# ------------------------------------------------------------ restart idempotency
def test_sweep_is_idempotent_across_restart(fdb: Path) -> None:
    _ep(fdb, "E", "AAA", "2026-07-20T14:00:00+00:00", anchor_price="100",
        anchor_atr14="5", anchor_direction="bull")
    _dot(fdb, "d1", "E", "AAA", "2026-07-20T14:00:00+00:00", trigger_price="100")
    now = datetime(2026, 7, 20, 17, 0, tzinfo=_UTC)

    def price(_s: str) -> Decimal:
        return Decimal("90.0")

    first = sweep_lifecycle(now, fdb, price_lookup=price)
    assert first["episodes_closed"] == 1
    closed_at = _closed_at(fdb, "E")

    # Simulate restart: sweep_lifecycle holds no memory state — a second call
    # (fresh connections) re-evaluates and is a no-op on the already-closed row.
    second = sweep_lifecycle(now, fdb, price_lookup=price)
    assert second["episodes_closed"] == 0
    assert _state(fdb, "E") == {"state": "closed", "close_reason": "price_invalidation"}
    assert _closed_at(fdb, "E") == closed_at  # the close is not re-stamped


def _closed_at(db: Path, episode_id: str) -> str:
    with connect(db) as conn:
        return conn.execute(
            "SELECT closed_at FROM fusion_episodes WHERE id = ?", (episode_id,)
        ).fetchone()[0]


# --------------------------------------------------------------------- migration
def test_migration_rebuilds_legacy_symbol_day_table(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    with connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE fusion_episodes (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market_day TEXT NOT NULL,
                opened_at TEXT NOT NULL, updated_at TEXT NOT NULL, anchor_dot_id TEXT,
                anchor_price TEXT, anchor_atr14 TEXT, anchor_direction TEXT,
                registration_called INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                direction_state TEXT NOT NULL DEFAULT 'mixed',
                contested INTEGER NOT NULL DEFAULT 0,
                signature TEXT NOT NULL DEFAULT '',
                market_shock INTEGER NOT NULL DEFAULT 0,
                UNIQUE(symbol, market_day)
            );
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, score, anchor_direction
            ) VALUES
                ('E1', 'AAA', '2026-07-10', '2026-07-10T14:00:00+00:00',
                 '2026-07-10T15:00:00+00:00', 3.2, 'bull'),
                ('E2', 'BBB', '2026-07-11', '2026-07-11T14:00:00+00:00',
                 '2026-07-11T15:00:00+00:00', 1.0, 'bear');
            """
        )
        conn.commit()

    init_fusion_tables(db)  # runs the guarded rebuild

    with connect(db) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, symbol, state, close_reason, closed_at, last_evidence_at "
                "FROM fusion_episodes ORDER BY id"
            ).fetchall()
        ]
        columns = {r[1] for r in conn.execute("PRAGMA table_info(fusion_episodes)")}
        # PRAGMA index_list columns: (seq, name, unique, origin, partial).
        indexes = [
            dict(name=r[1], unique=r[2], origin=r[3])
            for r in conn.execute("PRAGMA index_list(fusion_episodes)")
        ]

    # ids preserved so child rows stay linked.
    assert [r["id"] for r in rows] == ["E1", "E2"]
    # legacy symbol-day rows are closed + migrated, updated_at carried forward.
    assert all(r["state"] == "closed" and r["close_reason"] == "migrated" for r in rows)
    assert rows[0]["closed_at"] == "2026-07-10T15:00:00+00:00"
    assert rows[0]["last_evidence_at"] == "2026-07-10T15:00:00+00:00"
    # new columns present, partial-unique open index present.
    assert {"state", "bull_mass", "bear_mass", "conviction", "key_levels_json"} <= columns
    index_names = {i["name"] for i in indexes}
    assert "idx_fusion_episode_open" in index_names
    # the dead UNIQUE(symbol, market_day) is gone: no UNIQUE-CONSTRAINT autoindex
    # remains (origin 'u'); only the PK autoindex (origin 'pk') and the partial
    # open index (origin 'c') survive.
    assert not [i for i in indexes if i["origin"] == "u"]


def test_fresh_install_skips_migration_and_has_new_shape(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    init_fusion_tables(db)
    init_fusion_tables(db)  # second call must be a clean no-op (guarded)
    with connect(db) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(fusion_episodes)")}
    assert "state" in columns
