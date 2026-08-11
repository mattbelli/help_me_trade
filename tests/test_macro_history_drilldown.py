"""The regime snapshot must be interrogable, not just readable.

`market` used to `del filters` on its first line: 196,058 observations on disk, and
an agent could see only today's number and its percentile. Being told credit sits at
the 4.9th percentile of 1,318 observations, with no way to look at those observations,
means taking the rank on faith.
"""

from __future__ import annotations

import pytest

from ai.perception.resources import query
from db.macro import upsert_macro
from signals import macro_substrate


@pytest.fixture
def macro_db(tmp_path, monkeypatch):
    """Patching the module constant only redirects the store because `history`
    resolves it at call time; as a default argument it would bind at import and
    every test here would silently read the real 196,058-row store instead."""
    db = tmp_path / "macro.db"
    upsert_macro(
        [("VIXCLS", f"2026-01-{d:02d}", 10.0 + d) for d in range(1, 21)]
        + [("DGS10", f"2026-01-{d:02d}", 4.0) for d in range(1, 21)]
        + [("DGS2", f"2026-01-{d:02d}", 3.5) for d in range(1, 21)],
        db_path=db,
    )
    monkeypatch.setattr(macro_substrate, "DEFAULT_MACRO_DB_PATH", db)
    return db


@pytest.mark.asyncio
async def test_a_series_comes_back_newest_first(macro_db) -> None:
    """Newest-first is not cosmetic: the result cap drops from the TAIL, so the
    oldest end of a window is the right end to lose when one must be lost."""
    rows = (await query("market", {"series": "VIXCLS", "limit": 5}))["rows"]
    assert [r["date"] for r in rows] == [f"2026-01-{d}" for d in (20, 19, 18, 17, 16)]


@pytest.mark.asyncio
async def test_an_axis_name_resolves_to_the_series_the_snapshot_ranked(macro_db) -> None:
    """`credit` and `BAMLH0A0HYM2` must agree, or the drill-down would quietly
    answer a different question than the percentile it is checking."""
    by_axis = (await query("market", {"series": "vol", "limit": 3}))["rows"]
    by_id = (await query("market", {"series": "VIXCLS", "limit": 3}))["rows"]
    assert [r["value"] for r in by_axis] == [r["value"] for r in by_id]


@pytest.mark.asyncio
async def test_a_spread_axis_is_date_joined_not_two_levels(macro_db) -> None:
    """The house rule the fusion regime dot follows: never subtract across dates."""
    rows = (await query("market", {"series": "curve", "limit": 3}))["rows"]
    assert all(r["value"] == pytest.approx(0.5) for r in rows)


@pytest.mark.asyncio
async def test_a_window_selects_by_date(macro_db) -> None:
    rows = (await query("market", {"series": "VIXCLS",
                                   "since": "2026-01-05", "until": "2026-01-07"}))["rows"]
    assert sorted(r["date"] for r in rows) == ["2026-01-05", "2026-01-06", "2026-01-07"]


@pytest.mark.asyncio
async def test_an_empty_window_says_what_the_series_actually_covers(macro_db) -> None:
    """"No data" alone reads as "the app is broken" and sends an agent to the open
    web. The span it DOES cover is the answer to the question behind the question."""
    row = (await query("market", {"series": "VIXCLS", "since": "1999-01-01",
                                  "until": "1999-12-31"}))["rows"][0]
    assert row["unavailable"] is True
    assert "2026-01-01..2026-01-20" in row["note"]


@pytest.mark.asyncio
async def test_an_unknown_name_hands_back_the_menu(macro_db) -> None:
    """A bare error teaches the agent nothing; the list teaches it the vocabulary."""
    row = (await query("market", {"series": "vixx"}))["rows"][0]
    assert "unknown" in row["error"]
    assert "vol_term" in row["axes"] and "VIXCLS" in row["series"]


@pytest.mark.asyncio
async def test_no_series_filter_still_returns_the_snapshot(macro_db) -> None:
    """The drill-down is additive — every existing caller reads the snapshot."""
    rows = (await query("market"))["rows"]
    assert "series" not in rows[0]


def test_a_documented_depth_limit_is_stated_where_an_agent_will_hit_it() -> None:
    """Credit stops at 2021 because ICE truncates BAML OAS at FRED — measured
    07-28, not a gap we can fill. An agent told only "no data for 2020" would go
    shopping for it; one told why does not."""
    from signals.macro_substrate import DEPTH_NOTES, REPORT_AXES

    assert REPORT_AXES["credit"] in DEPTH_NOTES
    assert "ICE" in DEPTH_NOTES[REPORT_AXES["credit"]]


@pytest.mark.asyncio
async def test_the_windows_extremes_are_stated_not_left_to_be_inferred(macro_db) -> None:
    """The rows delivered are a slice; the high and low may sit outside it. Measured
    on a live turn: an agent handed 496 of 1,318 credit observations reported its
    visible high as the window's high, missing the real one by 1.38 points. Every
    disclosure was present and it generalised anyway — so state the extremes."""
    result = await query("market", {"series": "VIXCLS", "limit": 2})
    row = result["rows"][0]
    assert row["window_high"] == {"date": "2026-01-20", "value": 30.0}
    assert row["window_low"] == {"date": "2026-01-01", "value": 11.0}
    assert min(r["value"] for r in result["rows"]) > row["window_low"]["value"]


@pytest.mark.asyncio
async def test_the_extremes_respect_the_requested_window(macro_db) -> None:
    """They describe the since/until window the caller asked about, not the series."""
    row = (await query("market", {"series": "VIXCLS", "since": "2026-01-05",
                                  "until": "2026-01-08", "limit": 1}))["rows"][0]
    assert row["window_high"] == {"date": "2026-01-08", "value": 18.0}
    assert row["window_low"] == {"date": "2026-01-05", "value": 15.0}
