"""Flow decomposition: order clustering, characterization rules, generator gating."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from signals.options_flow.daily_report import (
    CAVEATS,
    analyze_window,
    characterize,
    cluster_orders,
)
from trading.market_calendar import is_monthly_opex, monthly_opex_date

_ET = ZoneInfo("America/New_York")


def _ts(h: int, m: int, s: int = 0, d: date = date(2026, 7, 17)) -> int:
    return int(datetime(d.year, d.month, d.day, h, m, s, tzinfo=_ET).timestamp() * 1000)


def _print(
    occ: str,
    u: str,
    price: float,
    size: int,
    dte: int,
    direction: str,
    ts_ms: int,
) -> dict:
    return {
        "ts_ms": ts_ms,
        "occ": occ,
        "u": u,
        "price": price,
        "size": size,
        "dte": dte,
        "dir": direction,
        "basis": "stream_quote",
    }


# --- market calendar -----------------------------------------------------------


def test_monthly_opex_third_friday() -> None:
    assert monthly_opex_date(2026, 7) == date(2026, 7, 17)
    assert is_monthly_opex(date(2026, 7, 17))
    assert not is_monthly_opex(date(2026, 7, 18))
    assert not is_monthly_opex(date(2026, 7, 10))


def test_monthly_opex_holiday_shifts_to_thursday() -> None:
    # Good Friday 2000-04-21 was the 3rd Friday of April — OpEx moved to Thursday.
    assert monthly_opex_date(2000, 4) == date(2000, 4, 20)


# --- clustering ----------------------------------------------------------------


def test_cluster_merges_same_second_sweep() -> None:
    ts = _ts(9, 40, 25)
    prints = [
        _print("SPX261016P07100000", "SPX", 125.32, 100, 91, "buy_side", ts),
        _print("SPX261016P07100000", "SPX", 125.32, 100, 91, "buy_side", ts + 3),
        _print("SPX261016P07100000", "SPX", 125.32, 100, 91, "buy_side", ts + 9),
    ]
    orders = cluster_orders(prints)
    assert len(orders) == 1
    o = orders[0]
    assert o["contracts"] == 300
    assert o["n_prints"] == 3
    assert o["stance"] == "bear"  # put buy
    assert o["premium_usd"] == pytest.approx(125.32 * 300 * 100)


def test_cluster_stance_mapping_and_dust_floor() -> None:
    ts = _ts(9, 45)
    prints = [
        _print("SPY260918C00640000", "SPY", 10.0, 50, 63, "buy_side", ts),  # call buy = bull
        _print("SPY260918P00600000", "SPY", 10.0, 50, 63, "sell_side", ts + 2000),  # put sell = bull
        _print("SPY260918C00640000", "SPY", 10.0, 50, 63, "sell_side", ts + 4000),  # call sell = bear
        _print("SPY260731P00620000", "SPY", 5.0, 1, 14, "buy_side", ts + 6000),  # $500 dust
        _print("SPY260918C00640000", "SPY", 10.0, 50, 63, "undetermined", ts + 8000),
    ]
    orders = cluster_orders(prints)
    stances = sorted(o["stance"] for o in orders)
    assert stances == ["bear", "bull", "bull"]  # dust + undetermined dropped


# --- window analysis + rules ---------------------------------------------------


def _hedge_block_day() -> list[dict]:
    """Index bear premium dominated by two long-tenor SPX blocks + small noise."""
    rows = [
        _print("SPX261016P07100000", "SPX", 125.0, 800, 91, "buy_side", _ts(9, 40, 25)),
        _print("SPX260918P07500000", "SPX", 195.0, 350, 63, "buy_side", _ts(9, 48, 8)),
    ]
    for i in range(5):
        rows.append(
            _print("SPY260721P00620000", "SPY", 2.0, 20, 3, "buy_side", _ts(9, 41 + i))
        )
    # A bullish single-name standout: AAPL calls bought, one-sided.
    rows.append(_print("AAPL260821C00230000", "AAPL", 12.0, 3000, 35, "buy_side", _ts(9, 42)))
    rows.append(_print("AAPL260821C00230000", "AAPL", 12.0, 2000, 35, "buy_side", _ts(9, 50)))
    # Outside the window — must be excluded.
    rows.append(_print("SPX261016P07100000", "SPX", 125.0, 900, 91, "buy_side", _ts(10, 30)))
    return rows


def test_analyze_window_lanes_and_window_filter() -> None:
    analysis = analyze_window(_hedge_block_day(), "open")
    idx = analysis["lanes"]["index"]
    # 800x@125 + 350x@195 = $16.825M bear; the 10:30 print excluded.
    assert idx["bear_usd"] == pytest.approx(800 * 125.0 * 100 + 350 * 195.0 * 100 + 5 * 20 * 2.0 * 100)
    assert idx["long_tenor_bear_usd"] == pytest.approx(800 * 125.0 * 100 + 350 * 195.0 * 100)
    sn = analysis["lanes"]["single_name"]
    assert sn["bull_usd"] == pytest.approx(5000 * 12.0 * 100)
    assert analysis["concentration"]["n_orders"] > 0


def test_characterize_hedge_blocks_and_opex_clause() -> None:
    analysis = analyze_window(_hedge_block_day(), "open")
    chars = characterize(analysis, is_opex=True)
    codes = [c["code"] for c in chars]
    assert "hedge_tenor_blocks" in codes
    hedge = next(c for c in chars if c["code"] == "hedge_tenor_blocks")
    assert "consistent with institutional hedging" in hedge["text"]
    assert "OpEx" in hedge["text"]
    no_opex = characterize(analysis, is_opex=False)
    hedge2 = next(c for c in no_opex if c["code"] == "hedge_tenor_blocks")
    assert "OpEx" not in hedge2["text"]


def test_characterize_single_name_standout() -> None:
    analysis = analyze_window(_hedge_block_day(), "open")
    chars = characterize(analysis, is_opex=False)
    standouts = [c for c in chars if c["code"] == "single_name_standout"]
    assert len(standouts) == 1
    assert "AAPL" in standouts[0]["text"]


def test_characterize_quiet_on_broad_flow() -> None:
    # Many small short-dated orders, no side dominated by blocks: no rule fires.
    rows = [
        _print("SPY260721C00630000", "SPY", 2.0, 30, 3, "buy_side", _ts(9, 31 + i))
        for i in range(20)
    ]
    analysis = analyze_window(rows, "open")
    assert characterize(analysis, is_opex=False) == []


# --- generator -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_flow_decomp_report(tmp_path: Path) -> None:
    import json as _json

    from db.index_pressure import record_day, upsert_minutes
    from signals.reports import generate_flow_decomp_report

    db = tmp_path / "app.db"
    d = "2026-07-17"
    for window in ("open", "close"):
        upsert_minutes(
            [("SPX", d, 5, 30_000.0, 16_825_000.0, 1_000_000.0, 90, "gauge_lane")],
            window=window,
            db_path=db,
        )
        record_day(
            date=d,
            window=window,
            session_type="full",
            minutes_observed=30,
            syms=1,
            coverage_modes="gauge_lane",
            writer_pid=1,
            finalized=True,
            db_path=db,
        )
    lab_dir = tmp_path / "research" / "print_lab"
    lab_dir.mkdir(parents=True)
    with (lab_dir / f"{d}.jsonl").open("w") as f:
        for row in _hedge_block_day():
            f.write(_json.dumps(row) + "\n")

    report = await generate_flow_decomp_report(
        db_path=db,
        et_date=date(2026, 7, 17),
        now_et=datetime(2026, 7, 17, 18, 0, tzinfo=_ET),
    )
    assert report is not None
    assert report["kind"] == "flow_decomp"
    assert report["format"] == "html"
    assert "OpEx" in report["title"]
    payload = report["payload"]
    assert payload["opex"] is True
    assert set(payload["windows"]) == {"open", "close"}
    assert payload["caveats"] == list(CAVEATS)
    assert any(c["code"] == "hedge_tenor_blocks" for c in payload["characterizations"])
    # Full order list must not bloat the payload; top orders survive.
    assert "orders" not in payload["windows"]["open"]
    assert payload["windows"]["open"]["top_orders"]
    # The scheduler's once-per-day guard must see this report (review C1: a
    # missing dedup_key means regenerate + re-push every 15-min poll).
    from db.reports import report_exists_for_day

    assert report_exists_for_day(
        "flow_decomp", date(2026, 7, 17), dedup_key="flow_decomp", db_path=db
    )


@pytest.mark.asyncio
async def test_generate_waits_for_close_finalize_same_day(tmp_path: Path) -> None:
    from db.index_pressure import record_day, upsert_minutes
    from signals.reports import generate_flow_decomp_report

    db = tmp_path / "app.db"
    d = "2026-07-17"
    upsert_minutes(
        [("SPX", d, 5, 1_000.0, 2_000.0, 0.0, 3, "gauge_lane")],
        window="open",
        db_path=db,
    )
    record_day(
        date=d,
        window="open",
        session_type="full",
        minutes_observed=30,
        syms=1,
        coverage_modes="gauge_lane",
        writer_pid=1,
            finalized=True,
        db_path=db,
    )
    # Same ET day, 16:05 — close window not finalized yet: hold off.
    early = await generate_flow_decomp_report(
        db_path=db,
        et_date=date(2026, 7, 17),
        now_et=datetime(2026, 7, 17, 16, 5, tzinfo=_ET),
    )
    assert early is None
    # After the grace cutoff the report generates, flagged incomplete.
    late = await generate_flow_decomp_report(
        db_path=db,
        et_date=date(2026, 7, 17),
        now_et=datetime(2026, 7, 17, 16, 25, tzinfo=_ET),
    )
    assert late is not None
    assert "close" not in late["payload"]["completeness"]


@pytest.mark.asyncio
async def test_generate_returns_none_when_no_data(tmp_path: Path) -> None:
    from signals.reports import generate_flow_decomp_report

    report = await generate_flow_decomp_report(
        db_path=tmp_path / "app.db",
        et_date=date(2026, 7, 16),
        now_et=datetime(2026, 7, 18, 12, 0, tzinfo=_ET),
    )
    assert report is None


@pytest.mark.asyncio
async def test_generate_skips_non_trading_day(tmp_path: Path) -> None:
    from signals.reports import generate_flow_decomp_report

    report = await generate_flow_decomp_report(
        db_path=tmp_path / "app.db",
        et_date=date(2026, 7, 18),  # Saturday
        now_et=datetime(2026, 7, 18, 12, 0, tzinfo=_ET),
    )
    assert report is None
