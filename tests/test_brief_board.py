"""Nightly brief board (Stage 1) — lane merge, degradation, sector math, resource."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from ai.perception.resources import query
from research.brief import board as bb

_CLASSES = {"SPY": "etf", "QQQ": "etf", "SPX": "index"}


def _classify(sym: str) -> str:
    return _CLASSES.get(sym, "equity")


def _hits(*syms: str) -> dict:
    return {"hits": [{"symbol": s, "why": f"lit {s}"} for s in syms], "newest": None}


# --- lane merge ---------------------------------------------------------------------------


def test_merge_dedupes_and_counts_angles_not_lanes() -> None:
    lanes = {
        "attention": _hits("AAA"),
        "rare_flow": _hits("AAA", "BBB"),
        "abnormality": _hits("AAA", "CCC"),
        "news": _hits("CCC"),
    }
    cands = bb.merge_lanes(lanes, _classify)
    by = {c["symbol"]: c for c in cands}
    assert set(by) == {"AAA", "BBB", "CCC"}
    # attention already IS range+flow: rare_flow and abnormality add lanes, not angles.
    assert by["AAA"]["lanes"] == ["attention", "rare_flow", "abnormality"]
    assert by["AAA"]["angles"] == ["flow", "range"]
    assert by["CCC"]["angles"] == ["news", "range"]
    assert by["BBB"]["angles"] == ["flow"]
    # >=2 angles rank first, then admission order.
    assert [c["symbol"] for c in cands] == ["AAA", "CCC", "BBB"]


def test_merge_drops_etfs_and_indexes() -> None:
    cands = bb.merge_lanes({"rare_flow": _hits("SPY", "SPX", "QQQ", "MU")}, _classify)
    assert [c["symbol"] for c in cands] == ["MU"]


def test_quota_caps_new_names_but_lit_names_still_credit() -> None:
    lanes = {
        "attention": _hits("AAA"),
        "technical": _hits("T1", "T2", "T3", "AAA"),
    }
    cands = bb.merge_lanes(lanes, _classify, quotas={"attention": 8, "technical": 2})
    assert [c["symbol"] for c in cands] == ["AAA", "T1", "T2"]  # T3 over quota
    # AAA sat beyond the quota in the technical list but is lit there: it credits.
    assert "technical" in cands[0]["lanes"]


def test_cap_and_errored_lane_skipped() -> None:
    lanes = {"attention": _hits(*[f"S{i}" for i in range(50)]), "news": {"error": "boom"}}
    cands = bb.merge_lanes(lanes, _classify, quotas={"attention": 50, "news": 3}, cap=30)
    assert len(cands) == 30


def test_own_norm_spikes() -> None:
    day = "2026-09-25"
    prior = [(date(2026, 9, 25) - timedelta(days=i)).isoformat() for i in range(1, 21)]
    counts = {
        "HOT": {day: 12, **{d: 1 for d in prior}},  # 12x its own norm
        "BUSY": {day: 30, **{d: 25 for d in prior}},  # big but normal for it
        "NEW": {day: 4},  # first-ever items: ranked by size, no divide-by-zero
        "TINY": {day: 2},
    }
    out = bb.own_norm_spikes(counts, day, prior, min_today=3, min_ratio=3.0)
    assert [r["symbol"] for r in out] == ["NEW", "HOT"]
    assert out[1]["ratio"] == 12.0


def test_technical_events() -> None:
    flat = [100.0] * 260
    assert bb.technical_events(flat[:-1] + [101.0]) == ["52w_high_break", "200d_cross_up"]
    assert bb.technical_events(flat[:-1] + [99.0]) == ["52w_low_break", "200d_cross_down"]
    assert bb.technical_events(flat) == []
    assert bb.technical_events(flat[:100]) == []


def test_fundamentals_snapshot() -> None:
    def q(end: str, val: float) -> dict:
        return {"period_end": end, "val": val, "fp": "Q3", "filed": "2026-06-25"}

    metrics = {
        "revenue": {"quarterly": [q("2026-05-28", 200.0), q("2026-02-26", 150.0),
                                  q("2025-05-29", 100.0)]},
        "gross_profit": {"quarterly": [q("2026-05-28", 80.0)]},
        "operating_income": {"quarterly": [q("2026-05-28", 50.0)]},
        "eps_diluted": {"quarterly": [q("2026-05-28", 1.5)]},
    }
    snap = bb.fundamentals_snapshot(metrics)
    assert snap["revenue_yoy_pct"] == 100.0
    assert snap["gross_margin_pct"] == 40.0
    assert snap["operating_margin_pct"] == 25.0
    assert snap["eps_diluted"] == 1.5
    assert bb.fundamentals_snapshot({}) is None


# --- sector math --------------------------------------------------------------------------


def _series(start: date, n: int, price) -> list[dict]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append({"d": d.isoformat(), "c": price(len(out))})
        d += timedelta(days=1)
    return out


def test_horizon_returns_windows_and_missing_history() -> None:
    bars = _series(date(2024, 1, 1), 600, lambda i: 100.0 + i)
    as_of = date.fromisoformat(bars[-1]["d"])
    ret = bb.horizon_returns(bars, as_of)
    one_y = ret["1y"]
    start = [b for b in bars if b["d"] <= bb._months_back(as_of, 12).isoformat()][-1]
    assert one_y["from"] == start["d"] and one_y["to"] == bars[-1]["d"]
    assert one_y["pct"] == round((bars[-1]["c"] / start["c"] - 1) * 100, 2)
    # ~2.3 years of data: 3y/5y/10y reach past the first bar -> None, not a short window.
    assert ret["3y"] is None and ret["10y"] is None


def test_sector_stats_relative_drawdown_and_200d() -> None:
    sector = _series(date(2024, 1, 1), 400, lambda i: 100.0 + i if i < 300 else 400.0 - (i - 300))
    spy = _series(date(2024, 1, 1), 400, lambda i: 100.0)
    as_of = date.fromisoformat(sector[-1]["d"])
    s = bb.sector_stats(sector, spy, as_of)
    assert s["rel_spy_pp"]["1m"] == s["returns"]["1m"]["pct"]  # flat SPY -> rel = own return
    assert s["ath_close"] == 400.0 and s["close"] == 301.0
    assert s["drawdown_from_ath_pct"] == round((301 / 400 - 1) * 100, 2)
    closes = [b["c"] for b in sector]
    assert s["sma200"] == round(sum(closes[-200:]) / 200, 2)
    assert s["vs_sma200_pct"] < 0
    # Bars after as_of are ignored.
    early = date.fromisoformat(sector[100]["d"])
    assert bb.sector_stats(sector, spy, early)["close"] == sector[100]["c"]


# --- graceful degradation -----------------------------------------------------------------


async def test_one_failing_section_leaves_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a):
        raise RuntimeError("reader down")

    async def index(_d):
        return {"gex": {"SPX": {"oi_as_of": "2026-09-25", "net_gamma_usd_per_1pct_b": 1.0}}}

    async def macro():
        return {"market": {"error": "market: offline"}, "book_frame_md": "frame"}

    monkeypatch.setattr(bb, "_candidates_section", boom)
    monkeypatch.setattr(bb, "_index_section", index)
    monkeypatch.setattr(bb, "_macro_section", macro)
    monkeypatch.setattr(bb, "_sectors_section", lambda _d: {"basis": "b", "sectors": {}})
    monkeypatch.setattr(bb, "_events_section", lambda *_a: {"next_session": "2026-09-28"})

    board = await bb.abuild_board(date(2026, 9, 25))
    assert board["candidates"] == {"error": "RuntimeError: reader down"}
    assert board["index"]["gex"]["SPX"]["net_gamma_usd_per_1pct_b"] == 1.0
    assert board["next_session"] == "2026-09-28"
    fresh = board["freshness"]
    assert fresh["index.gex.SPX"]["status"] == "ok"
    assert fresh["lane.attention"]["status"] == "MISSING"
    assert not bb.board_is_empty(board)
    md = bb.render_board_md(board)
    assert "ERROR: RuntimeError: reader down" in md and "## Freshness" in md


def test_board_empty_and_stale_flag() -> None:
    board = {"candidates": {"cards": []}, "index": {"gex": {"SPX": {"error": "x"}},
                                                    "futures": {"unavailable": "no reader"}}}
    assert bb.board_is_empty(board)
    board["macro"] = {"market": {"axes": {"vol": {"as_of": "2026-09-17"},
                                          "credit": {"as_of": "2026-09-24"}}}}
    fresh = bb.freshness(board, date(2026, 9, 25))
    assert fresh["macro.axis.vol"]["status"] == "STALE"  # 6 sessions behind, 3 allowed
    assert fresh["macro.axis.credit"]["status"] == "ok"


def test_gex_refuses_a_mislabelled_index(monkeypatch: pytest.MonkeyPatch) -> None:
    async def rows(_res, _f):
        return [{"index": "SPX", "spot": 1.0}]

    monkeypatch.setattr(bb, "_rows", rows)
    import asyncio

    with pytest.raises(RuntimeError, match="answered 'SPX' for index='NDX'"):
        asyncio.run(bb._gex("NDX"))


# --- storage + agent resource -------------------------------------------------------------


def _stored_board() -> dict:
    card = {"symbol": "MU", "angles": ["flow", "range"], "lanes": ["attention"],
            "why": {"attention": "lit"}, "day": {"close": 1.0}}
    return {
        "et_date": "2026-09-25",
        "generated_at": "2026-09-25T22:00:00+00:00",
        "note": "perception",
        "candidates": {"lanes": {"attention": {"quota": 8, "lit": 1, "admitted": ["MU"],
                                               "newest": None}},
                       "cards": [card, {**card, "symbol": "AMD"}]},
        "index": {"gex": {}},
        "macro": {}, "sectors": {}, "events": {},
        "freshness": {"lane.news": {"status": "STALE", "newest": "x", "allowed_lag_sessions": 0}},
    }


async def test_brief_board_resource(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bb, "BRIEFS_DIR", tmp_path)
    missing = await query("brief_board", {})
    assert missing["rows"][0]["unavailable"] is True

    out_dir = bb.save_board(_stored_board())
    assert (out_dir / "board.json").exists() and (out_dir / "board.md").exists()
    assert out_dir.parent == tmp_path

    overview = (await query("brief_board", {}))["rows"][0]
    assert [c["symbol"] for c in overview["candidates"]] == ["MU", "AMD"]
    assert list(overview["not_fresh"]) == ["lane.news"]

    cards = await query("brief_board", {"section": "candidates", "date": "2026-09-25"})
    assert [r["symbol"] for r in cards["rows"]] == ["MU", "AMD"]

    one = await query("brief_board", {"symbol": "amd"})
    assert [r["symbol"] for r in one["rows"]] == ["AMD"]

    idx = await query("brief_board", {"section": "index"})
    assert idx["rows"][0]["index"] == {"gex": {}}

    bad = await query("brief_board", {"section": "nope"})
    assert "unknown section" in bad["rows"][0]["error"]

    assert (await query("brief_board", {"date": "2026-01-02"}))["rows"][0]["unavailable"]


def test_book_pack_line_never_reads_real_data_for_tmp_db(tmp_path: Path) -> None:
    from research.books import _brief_board_line

    assert _brief_board_line(tmp_path / "t.db") is None


def test_fill_index_otm_uses_close_spot_and_store_sign() -> None:
    prints = [
        {"root": "SPXW", "right": "call", "strike": 110.0, "otm_pct": None},
        {"root": "NDXP", "right": "put", "strike": 90.0, "otm_pct": None},
        {"root": "SPY", "right": "put", "strike": 90.0, "otm_pct": 3.0},  # store value kept
    ]
    bb.fill_index_otm(prints, {"SPX": 100.0, "NDX": 100.0})
    assert prints[0]["otm_pct_vs_close_spot"] == 10.0
    assert prints[1]["otm_pct_vs_close_spot"] == 10.0  # put below spot = OTM, positive
    assert "otm_pct_vs_close_spot" not in prints[2]
