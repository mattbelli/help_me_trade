"""The pre-close IV reading — a separate table and a session-relative window.

Two things can silently corrupt the 3:45-vs-close comparison: a pre-close row
landing in ``iv_daily`` (every reader there treats one row per session as the
close), and a fixed-clock window reading a 13:00 half-day after it has closed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db.base import connect
from db.options_chain_history import init_chain_tables, write_iv_daily, write_iv_preclose
from tools.options_chain_sampler import preclose_window_open

_ET = ZoneInfo("America/New_York")


def _et(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=_ET)


def test_preclose_rows_never_land_in_iv_daily(tmp_path: Path) -> None:
    p = tmp_path / "chain.db"
    init_chain_tables(p)
    write_iv_daily([{"session": "2026-08-20", "underlying": "SPY", "atm_iv_30d": 0.15}], db_path=p)
    write_iv_preclose(
        [{"session": "2026-08-20", "underlying": "SPY", "atm_iv_30d": 0.17, "tier": "wide"}],
        db_path=p,
    )
    with connect(p) as conn:
        close = conn.execute("SELECT atm_iv_30d, tier FROM iv_daily").fetchall()
        pre = conn.execute("SELECT atm_iv_30d, tier FROM iv_preclose").fetchall()
    assert [tuple(r) for r in close] == [(0.15, "deep")]
    assert [tuple(r) for r in pre] == [(0.17, "wide")]


def test_window_is_the_last_10_to_20_minutes_of_a_full_day() -> None:
    # 2026-08-20 is a Thursday, full session, EDT.
    assert preclose_window_open(_et(2026, 8, 20, 15, 45))
    assert preclose_window_open(_et(2026, 8, 20, 15, 40))
    assert preclose_window_open(_et(2026, 8, 20, 15, 50))
    assert not preclose_window_open(_et(2026, 8, 20, 15, 30))
    assert not preclose_window_open(_et(2026, 8, 20, 15, 55))
    assert not preclose_window_open(_et(2026, 8, 20, 14, 45))  # the 19:45 UTC winter-slot firing
    assert not preclose_window_open(_et(2026, 8, 20, 16, 45))  # the 20:45 UTC summer-slot firing


def test_window_follows_the_13_00_close_on_a_half_day() -> None:
    # Day after Thanksgiving 2026 = Friday Nov 27, EST.
    assert preclose_window_open(_et(2026, 11, 27, 12, 45))
    assert not preclose_window_open(_et(2026, 11, 27, 15, 45))  # market already closed


def test_window_is_closed_on_a_non_trading_day() -> None:
    assert not preclose_window_open(_et(2026, 8, 22, 15, 45))  # Saturday
