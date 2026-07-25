"""Universe tiering — the append-only guarantees that breadth must not break.

The universe table is append-only because an IV rank is a percentile against a
symbol's own trailing year, so a hole is poison. Adding a wide breadth tier put two
new ways to corrupt it on the table: demoting a name we trade, and writing slice
aggregates where a reader expects board totals. Both are tested here.
"""

from __future__ import annotations

from pathlib import Path

from db.options_chain_history import (
    init_chain_tables,
    register_universe,
    universe_symbols,
    universe_tiers,
    write_iv_daily,
)


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "chain.db"
    init_chain_tables(p)
    return p


def test_wide_registration_never_demotes_a_deep_symbol(tmp_path: Path) -> None:
    """Breadth must not cost depth on a name we might actually hold."""
    p = _db(tmp_path)
    register_universe(["SPY", "AAPL"], source="core", tier="deep", db_path=p)
    register_universe(["AAPL", "KO"], source="corpus-breadth", tier="wide", db_path=p)
    tiers = universe_tiers(db_path=p)
    assert tiers["AAPL"] == "deep"
    assert tiers["KO"] == "wide"
    assert tiers["SPY"] == "deep"


def test_deep_registration_promotes_a_wide_symbol(tmp_path: Path) -> None:
    """Promotion is the only tier change there is — a watchlist add deepens it."""
    p = _db(tmp_path)
    register_universe(["KO"], source="corpus-breadth", tier="wide", db_path=p)
    assert universe_tiers(db_path=p)["KO"] == "wide"
    register_universe(["KO"], source="watchlist", tier="deep", db_path=p)
    assert universe_tiers(db_path=p)["KO"] == "deep"


def test_universe_symbols_can_be_filtered_by_tier(tmp_path: Path) -> None:
    p = _db(tmp_path)
    register_universe(["SPY"], tier="deep", db_path=p)
    register_universe(["KO", "XOM"], tier="wide", db_path=p)
    assert universe_symbols(db_path=p) == ["KO", "SPY", "XOM"]
    assert universe_symbols(db_path=p, tier="wide") == ["KO", "XOM"]
    assert universe_symbols(db_path=p, tier="deep") == ["SPY"]


def test_iv_daily_records_the_tier_so_slice_zeros_are_not_read_as_totals(tmp_path: Path) -> None:
    """A wide row's volume columns are placeholders, not measurements."""
    p = _db(tmp_path)
    write_iv_daily(
        [
            {"session": "2026-07-24", "underlying": "SPY", "atm_iv_30d": 0.15,
             "call_volume": 1_000, "put_volume": 2_000, "tier": "deep"},
            {"session": "2026-07-24", "underlying": "KO", "atm_iv_30d": 0.22,
             "call_volume": 0, "put_volume": 0, "tier": "wide"},
        ],
        db_path=p,
    )
    from db.base import connect

    with connect(p) as conn:
        rows = {
            r["underlying"]: (r["tier"], r["call_volume"])
            for r in conn.execute("SELECT underlying, tier, call_volume FROM iv_daily")
        }
    assert rows["SPY"] == ("deep", 1_000)
    assert rows["KO"] == ("wide", 0)


def test_tier_column_is_added_to_a_pre_tier_database(tmp_path: Path) -> None:
    """The live DB predates tiering; its existing rows are names we trade."""
    from db.base import connect

    p = tmp_path / "old.db"
    with connect(p) as conn:
        conn.execute(
            "CREATE TABLE sampler_universe (underlying TEXT PRIMARY KEY,"
            " first_seen TEXT NOT NULL, source TEXT)"
        )
        conn.execute("INSERT INTO sampler_universe VALUES ('SPY', '2026-07-25', 'core')")
        conn.commit()

    init_chain_tables(p)  # migration runs here
    assert universe_tiers(db_path=p) == {"SPY": "deep"}
