"""Regime macro table — upsert and latest observation reads."""

from __future__ import annotations

from pathlib import Path

from db.regime import latest_observation, recent_observations, upsert_regime


def test_upsert_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "regime.db"
    rows = [
        ("DGS10", "2026-06-20", 4.5),
        ("DGS2", "2026-06-20", 4.2),
    ]
    assert upsert_regime(rows, db_path=db) == 2
    assert upsert_regime(rows, db_path=db) == 2
    assert upsert_regime([("DGS10", "2026-06-20", 4.55)], db_path=db) == 1
    assert latest_observation("DGS10", db_path=db) == ("2026-06-20", 4.55)


def test_latest_observation_respects_as_of(tmp_path: Path) -> None:
    db = tmp_path / "regime.db"
    upsert_regime(
        [
            ("DGS10", "2026-06-18", 4.4),
            ("DGS10", "2026-06-20", 4.5),
            ("DGS10", "2026-06-24", 4.6),
        ],
        db_path=db,
    )
    assert latest_observation("DGS10", db_path=db) == ("2026-06-24", 4.6)
    assert latest_observation("DGS10", as_of="2026-06-22", db_path=db) == (
        "2026-06-20",
        4.5,
    )
    assert latest_observation("DGS10", as_of="2026-06-18", db_path=db) == (
        "2026-06-18",
        4.4,
    )
    assert latest_observation("DGS10", as_of="2026-06-17", db_path=db) is None


def test_recent_observations_newest_first_respects_limit_and_as_of(tmp_path: Path) -> None:
    db = tmp_path / "regime.db"
    upsert_regime(
        [
            ("VIXCLS", "2026-06-10", 18.0),
            ("VIXCLS", "2026-06-12", 19.0),
            ("VIXCLS", "2026-06-18", 20.0),
            ("VIXCLS", "2026-06-24", 30.0),
        ],
        db_path=db,
    )
    assert recent_observations("VIXCLS", limit=2, db_path=db) == [
        ("2026-06-24", 30.0),
        ("2026-06-18", 20.0),
    ]
    assert recent_observations("VIXCLS", limit=10, as_of="2026-06-20", db_path=db) == [
        ("2026-06-18", 20.0),
        ("2026-06-12", 19.0),
        ("2026-06-10", 18.0),
    ]
    assert recent_observations("VIXCLS", limit=5, db_path=tmp_path / "empty.db") == []
