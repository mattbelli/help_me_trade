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

    async def index(_d, _mode):
        return {"gex": {"SPX": {"oi_as_of": "2026-09-25", "net_gamma_usd_per_1pct_b": 1.0}}}

    async def macro(_d):
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


# --- weekly board ---------------------------------------------------------------------------


def test_week_sessions_and_stats() -> None:
    assert bb.week_sessions(date(2026, 9, 25)) == [date(2026, 9, d) for d in (21, 22, 23, 24, 25)]
    # Good Friday 2026-04-03: the week ending Thursday 04-02 reaches back to 03-27.
    assert bb.week_sessions(date(2026, 4, 2))[0] == date(2026, 3, 27)
    bars = [{"t": f"2026-09-{d:02d}T04:00:00Z", "o": 1, "h": 100 + d, "l": 90 + d, "c": 95 + d}
            for d in (18, 21, 22, 23, 24, 25)]
    w = bb.week_stats(bars, bb.week_sessions(date(2026, 9, 25)))
    assert w["from"] == "2026-09-18" and w["to"] == "2026-09-25"
    assert w["return_pct"] == round((120 / 113 - 1) * 100, 2)
    assert (w["high"], w["low"], w["sessions_with_bars"]) == (125, 111, 5)


def test_week_lanes_rank_multi_day_names_first() -> None:
    per_day = {
        "2026-09-23": _hits("OLD", "MULTI"),
        "2026-09-24": {"error": "reader down"},
        "2026-09-25": _hits("NEW", "MULTI"),
    }
    lane = bb.combine_days(per_day)
    assert [h["symbol"] for h in lane["hits"]] == ["MULTI", "NEW", "OLD"]
    assert lane["hits"][0]["sessions"] == ["2026-09-23", "2026-09-25"]
    assert lane["hits"][0]["why"].startswith("[2026-09-23, 2026-09-25] lit MULTI")
    assert lane["session_errors"] == {"2026-09-24": "reader down"}
    assert "error" in bb.combine_days({"2026-09-25": {"error": "x"}})

    lanes = {"rare_flow": lane, "news": bb.combine_days({"2026-09-25": _hits("NEW")})}
    cands = bb.merge_lanes(lanes, _classify, quotas={"rare_flow": 2, "news": 3})
    # Quota 2 admits the multi-day name first; NEW is admitted and gets news' angle too.
    assert [c["symbol"] for c in cands] == ["NEW", "MULTI"]
    assert cands[1]["sessions"] == ["2026-09-23", "2026-09-25"]
    # Same angles: more sessions rank first.
    tie = bb.merge_lanes({"rare_flow": lane}, _classify)
    assert [c["symbol"] for c in tie] == ["MULTI", "NEW", "OLD"]
    # The nightly shape (no sessions on hits) stays exactly as it was.
    assert "sessions" not in bb.merge_lanes({"rare_flow": _hits("AAA")}, _classify)[0]


def _fbars(start_utc: str, n: int, price: float, step: float = 0.0) -> list[dict]:
    from datetime import datetime as dt

    t0 = dt.fromisoformat(start_utc.replace("Z", "+00:00"))
    return [{"ts": (t0 + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "open": price + i * step, "close": price + i * step} for i in range(n)]


_WALLS = {"SPX": {"expiries": {"2026-10-16": [{"strike": 7500.0}, {"strike": 8000.0}]}},
          "SPXW": {"expiries": {"2026-09-28": [{"strike": 7700.0}, {"strike": 7600.0}],
                                "2026-09-29": [{"strike": 7650.0}]}}}


def test_futures_reaction_basis_and_implied_level() -> None:
    # Friday 2026-09-25 (EDT): cash close 16:00 ET = 20:00Z; Globex reopens Sunday 18:00 ET.
    bars = (_fbars("2026-09-25T19:55:00Z", 5, 7800.0)          # into the cash close
            + _fbars("2026-09-25T20:00:00Z", 60, 7801.0)        # 16:00-17:00 ET
            + _fbars("2026-09-27T22:00:00Z", 31, 7760.0, -1.5))  # Sunday from the reopen
    r = bb.futures_reaction(bars, date(2026, 9, 25), 7741.99)
    assert r["at_cash_close"] == {"ts": "2026-09-25T19:59:00Z", "price": 7800.0}
    assert r["reopen"] == {"ts": "2026-09-27T22:00:00Z", "open": 7760.0}
    assert r["latest"]["price"] == 7715.0
    assert r["basis"] == 58.01 and r["implied_cash"] == 7656.99
    assert r["reopen_vs_close_pct"] == -0.51 and r["latest_vs_close_pct"] == -1.09
    gex = {"spot": 7741.99, "gamma_flip_spot": 7668.84}
    lv = bb.implied_vs_levels(r["implied_cash"], gex, _WALLS, ("SPX", "SPXW"))
    assert lv["line"].startswith("implied 7,656.99 = below the 7,668.84 gamma flip (-0.15%)")
    # Nearest expiry per root: SPX 7500/8000, SPXW 7600/7700 (09-29 not read).
    assert (lv["oi_strike_below"], lv["oi_strike_above"]) == (7600.0, 7700.0)
    assert lv["line"].endswith("between the top-OI strikes 7,600 and 7,700 (nearest expiry)")
    assert "flip unavailable" in bb.implied_vs_levels(7656.99, {"error": "x"}, {}, ("NDX",))["line"]
    assert "implied_cash" not in bb.futures_reaction(bars, date(2026, 9, 25), None)
    assert "unavailable" in bb.futures_reaction(bars[10:], date(2026, 9, 25), 7741.99)


def _futures_db(path: Path) -> Path:
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE bars (symbol TEXT, timeframe TEXT, ts TEXT, open REAL, "
                     "high REAL, low REAL, close REAL, volume REAL)")
        for sym, px in (("/ES", 7800.0), ("/NQ", 27000.0)):
            for b in _fbars("2026-09-25T19:58:00Z", 2, px) + _fbars("2026-09-27T22:00:00Z", 2, px - 40):
                conn.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)",
                             (sym, "1Min", b["ts"], b["open"], 0, 0, b["close"], 1))
    return path


async def test_futures_section_is_fail_soft_when_schwab_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brokers.schwab_market_data as smd

    def down() -> None:
        raise ConnectionError("schwab down")

    monkeypatch.setattr(smd.SchwabMarketData, "from_settings", staticmethod(down))
    db = _futures_db(tmp_path / "f.db")
    gex = {"SPX": {"spot": 7741.99, "gamma_flip_spot": 7668.84,
                   "priced_at": "2026-09-25T19:59:59.9Z"},
           "NDX": {"error": "gex reader answered 'SPX' for index='NDX'"}}
    out = await bb._futures_section(date(2026, 9, 25), gex, _WALLS, db)
    assert out["refresh"].startswith("ConnectionError") and "stored bars" in out["note"]
    assert out["/ES"]["implied_cash"] == 7741.99 - 40
    assert "above the 7,668.84 gamma flip (+0.43%)" in out["/ES"]["levels"]["line"]
    assert "implied_cash" not in out["/NQ"] and "NDX gex spot" in out["/NQ"]["no_implied"]
    md = "\n".join(bb._render_futures(out))
    assert "Schwab refresh failed" in md and "implied SPX 7,701.99" in md


async def test_futures_refresh_is_one_pull_per_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brokers.schwab_market_data as smd
    import tools.futures_bars_archive as arch

    pulls: list[tuple] = []

    async def fake_pull(md, conn, symbol, timeframe, days_back, window_days):
        pulls.append((symbol, timeframe, days_back, window_days))
        return 0

    monkeypatch.setattr(smd.SchwabMarketData, "from_settings", staticmethod(lambda: object()))
    monkeypatch.setattr(arch, "_pull", fake_pull)
    assert await bb.refresh_futures(_futures_db(tmp_path / "f.db")) is None
    # days_back == window_days: _pull's loop makes exactly one request per symbol.
    assert pulls == [("/ES", "1Min", 3, 3), ("/NQ", "1Min", 3, 3)]


def test_weekend_headlines_rank_and_filter() -> None:
    items = [
        {"headline": "$100 Invested In Merck 5 Years Ago Would Be Worth This Much Today",
         "symbols": ["MRK"], "published_at": "2026-09-26T10:00:00Z"},
        {"headline": "If You Invested $100 In CrowdStrike Stock 5 Years Ago, You Would Have This Much",
         "symbols": ["CRWD"], "published_at": "2026-09-26T12:00:00Z"},
        {"headline": "Small cap files shelf", "symbols": ["ZZZ"], "published_at": "2026-09-26T11:00:00Z"},
        {"headline": "AKAM deal detail", "symbols": ["AKAM"], "published_at": "2026-09-26T09:00:00Z"},
        {"headline": "Trump rejects Iran ceasefire", "symbols": ["EIS", "EZU", "FXI", "ISRA", "KSA",
                                                                 "LNGX", "SPY"],
         "published_at": "2026-09-26T06:00:00Z"},
        {"headline": "Trump rejects Iran ceasefire (UPDATED)", "symbols": ["SPY"],
         "published_at": "2026-09-26T07:00:00Z"},
        {"headline": "Court fight lost", "symbols": [], "published_at": "2026-09-25T21:05:00Z"},
    ]
    out = bb.rank_headlines(items, {"AKAM"})
    assert out[0]["symbols"][0] == "SPY"  # the broad tag shows first
    assert [h["headline"] for h in out] == [
        "Trump rejects Iran ceasefire (UPDATED)", "Court fight lost", "AKAM deal detail",
        "Small cap files shelf"]


def test_weekly_board_storage_and_resource(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr(bb, "BRIEFS_DIR", tmp_path)
    nightly = _stored_board()
    weekly = {**_stored_board(), "mode": "weekly",
              "index": {"gex": {}, "futures": {"refresh": "ok", "caveat": "c",
                                                "/ES": {"index": "SPX", "unavailable": "no bars"}}},
              "weekend_news": {"headlines": [{"headline": "h"}]}}
    assert bb.save_board(weekly).name == "2026-09-25-weekly"
    assert bb.save_board(nightly).name == "2026-09-25"
    assert bb.load_board()["mode"] == "weekly"  # Sunday's weekly is newer than Friday's nightly
    assert bb.load_board("2026-09-25").get("mode") is None
    idx = asyncio.run(query("brief_board", {"date": "2026-09-25-weekly", "section": "index"}))
    assert idx["rows"][0]["index"]["futures"]["/ES"]["unavailable"] == "no bars"
    wn = asyncio.run(query("brief_board", {"date": "2026-09-25-weekly", "section": "weekend_news"}))
    assert wn["rows"][0]["weekend_news"]["headlines"][0]["headline"] == "h"
    md = (tmp_path / "2026-09-25-weekly" / "board.md").read_text()
    assert "(weekly" in md and "## Weekend headlines" in md


async def test_week_lanes_count_only_days_inside_the_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    # HABIT fires every day but never makes the day's top 4; RARE tops one day only.
    async def day_lanes(day: date, _nxt: date) -> dict:
        lanes = {lane: {"hits": [], "newest": None} for lane in bb.LANE_QUOTAS}
        top = [{"symbol": f"T{day.day}{i}", "why": ""} for i in range(4)]
        if day == date(2026, 9, 23):
            top[0] = {"symbol": "RARE", "why": ""}
        lanes["abnormality"]["hits"] = top + [{"symbol": "HABIT", "why": ""}]
        lanes["held"]["hits"] = [{"symbol": f"H{day.day}", "why": ""}]
        return lanes

    monkeypatch.setattr(bb, "_day_lanes", day_lanes)
    monkeypatch.setattr(bb, "_liquidity_floor", lambda _d: lambda _s: True)
    out = await bb._week_lanes(date(2026, 9, 25))
    syms = {h["symbol"]: h["sessions"] for h in out["abnormality"]["hits"]}
    assert "HABIT" not in syms
    assert syms["RARE"] == ["2026-09-23"]
    assert [h["symbol"] for h in out["held"]["hits"]] == ["H25"]  # the board's session only
