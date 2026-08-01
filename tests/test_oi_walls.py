"""Standing OI walls — store reader + the perception resource's honesty rails."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from ai.perception.resources import _read_oi_walls, query
from db import oi_walls


def _store(tmp_path: Path, rows: list[tuple]) -> Path:
    db = tmp_path / "oi_walls.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE oi_daily (date TEXT NOT NULL, source TEXT NOT NULL,"
        " root TEXT NOT NULL, expiry TEXT NOT NULL, cp TEXT NOT NULL,"
        " strike REAL NOT NULL, oi INTEGER NOT NULL,"
        " PRIMARY KEY (date, source, root, expiry, cp, strike))"
    )
    conn.executemany("INSERT INTO oi_daily VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    oi_walls._sources.cache_clear()
    return db


def _collar_rows(as_of: str, prior: str) -> list[tuple]:
    """A collar-shaped triple plus an unrelated single leg, on two dates."""
    legs = [("P", 5990.0, 24377), ("P", 7090.0, 24377), ("C", 7890.0, 24373)]
    rows = []
    for d in (prior, as_of):
        for cp, strike, oi in legs:
            rows.append((d, "sme", "SME", "2026-09", cp, strike, oi - (5 if d == prior else 0)))
        rows.append((d, "sme", "SME", "2026-08", "P", 6050.0, 4291))
    return rows


def test_triple_is_shape_matched_not_hardcoded(tmp_path: Path) -> None:
    db = _store(tmp_path, _collar_rows("2026-07-31", "2026-07-30"))
    rows = oi_walls.standing_walls(limit=10, db_path=db)
    flagged = {(r["cp"], r["strike"]) for r in rows if r.get("structure")}
    assert flagged == {("P", 5990.0), ("P", 7090.0), ("C", 7890.0)}
    # the lone 2026-08 leg is not a triple and must not be dressed as one
    assert all(r.get("structure") is None for r in rows if r["expiry"] == "2026-08")


def test_triple_needs_near_equal_legs(tmp_path: Path) -> None:
    rows = _collar_rows("2026-07-31", "2026-07-30")
    # break the match: one leg twice the size of the others is three trades, not one
    rows = [r if r[5] != 7890.0 else (*r[:6], 60000) for r in rows]
    db = _store(tmp_path, rows)
    assert all(r.get("structure") is None for r in oi_walls.standing_walls(limit=10, db_path=db))


def test_change_is_measured_against_the_previous_stored_date(tmp_path: Path) -> None:
    db = _store(tmp_path, _collar_rows("2026-07-31", "2026-07-30"))
    row = next(r for r in oi_walls.standing_walls(limit=10, db_path=db) if r["strike"] == 5990.0)
    assert row["as_of"] == "2026-07-31"
    assert (row["oi"], row["oi_prior"], row["oi_prior_date"]) == (24377, 24372, "2026-07-30")
    assert row["oi_change"] == 5
    assert row["days_standing"] == 2


def test_first_seen_is_null_when_the_strike_predates_the_window(tmp_path: Path) -> None:
    # A sighting on the window edge means the strike is OLDER than the window; calling
    # that an onset date would invent a fact about when the position was established.
    as_of = date.today()
    edge = as_of - timedelta(days=oi_walls.HISTORY_DAYS)
    db = _store(tmp_path, [
        (edge.isoformat(), "sme", "SME", "2026-09", "P", 5990.0, 24000),
        (as_of.isoformat(), "sme", "SME", "2026-09", "P", 5990.0, 24377),
    ])
    assert oi_walls.standing_walls(limit=5, db_path=db)[0]["first_seen"] is None


def test_stale_source_is_disclosed_on_every_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    old = (date.today() - timedelta(days=15)).isoformat()
    db = _store(tmp_path, [(old, "opra", "SPX", "2026-09-18", "P", 7000.0, 241469)])
    monkeypatch.setattr(oi_walls, "OI_WALLS_DB_PATH", db)
    rows = _read_oi_walls({}, db_path=tmp_path / "unused.db")
    assert rows[0]["as_of_age_days"] == 15
    assert "STALE" in rows[0]["caveat"]


def test_caveat_refuses_the_pin_claim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = _store(tmp_path, _collar_rows(date.today().isoformat(), "2026-07-30"))
    monkeypatch.setattr(oi_walls, "OI_WALLS_DB_PATH", db)
    caveat = _read_oi_walls({}, db_path=tmp_path / "unused.db")[0]["caveat"]
    assert "no pin, magnet or support/resistance edge has been measured" in caveat
    assert "not an identification of any named fund" in caveat


def test_missing_store_says_so_instead_of_raising(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(oi_walls, "OI_WALLS_DB_PATH", tmp_path / "absent.db")
    rows = _read_oi_walls({}, db_path=tmp_path / "unused.db")
    assert rows[0]["unavailable"] is True


@pytest.mark.asyncio
async def test_collar_alias_resolves() -> None:
    for alias in ("collar", "jpm_collar", "oi", "walls", "open_interest"):
        assert (await query(alias)).get("resource") == "oi_walls", alias
