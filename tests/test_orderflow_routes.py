"""Tests for orderflow API endpoints.

Uses a real MBO parquet (the winner post_id) to verify JSON shapes, value
correctness, and book orientation.  Runs against a minimal FastAPI app that
only includes the orderflow router — avoids importing api.main's expensive
lifespan (broker polling, db init, etc.).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.orderflow_routes import BOOK_DEPTH, router

_app = FastAPI()
_app.include_router(router)
client = TestClient(_app)

# The preferred winner — the Apr-21 long that hit +19 pts target; parquet verified present
WINNER_ID = "1914389641555558635"


# ─────────────────────────────────────────────────────────────────────────────
# /api/orderflow/calls
# ─────────────────────────────────────────────────────────────────────────────

def test_calls_200():
    resp = client.get("/api/orderflow/calls")
    assert resp.status_code == 200


def test_calls_nonempty():
    data = client.get("/api/orderflow/calls").json()
    assert "calls" in data
    assert len(data["calls"]) > 0


def test_calls_required_fields():
    calls = client.get("/api/orderflow/calls").json()["calls"]
    for c in calls:
        for field in ("post_id", "date", "direction", "level", "points_net", "exit_reason", "has_mbo"):
            assert field in c, f"calls: missing field {field!r}"


def test_winner_has_mbo_true():
    calls = client.get("/api/orderflow/calls").json()["calls"]
    winner = next((c for c in calls if c["post_id"] == WINNER_ID), None)
    assert winner is not None, f"Winner {WINNER_ID} not in calls list"
    assert winner["has_mbo"] is True


def test_calls_has_mbo_is_bool():
    calls = client.get("/api/orderflow/calls").json()["calls"]
    for c in calls:
        assert isinstance(c["has_mbo"], bool), f"has_mbo must be bool, got {type(c['has_mbo'])}"


# ─────────────────────────────────────────────────────────────────────────────
# /api/orderflow/{post_id}/meta
# ─────────────────────────────────────────────────────────────────────────────

def test_meta_200():
    assert client.get(f"/api/orderflow/{WINNER_ID}/meta").status_code == 200


def test_meta_404_unknown_id():
    assert client.get("/api/orderflow/9999999999999999999/meta").status_code == 404


def test_meta_required_fields():
    data = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    for field in (
        "post_id", "symbol", "post_ts", "entry_ts", "exit_ts", "exit_px",
        "level", "direction", "points_net", "exit_reason",
        "window_start", "window_end", "tz",
    ):
        assert field in data, f"meta: missing field {field!r}"


def test_meta_tz():
    data = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    assert data["tz"] == "America/New_York"


def test_meta_times_are_epoch_ms():
    data = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    for field in ("post_ts", "entry_ts", "exit_ts", "window_start", "window_end"):
        assert isinstance(data[field], int), f"meta.{field} should be int (epoch-ms)"
        # sanity: epoch-ms for 2020–2030 range
        assert 1_577_836_800_000 < data[field] < 1_893_456_000_000, (
            f"meta.{field}={data[field]} looks out of range"
        )


def test_meta_window_ordering():
    data = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    assert data["window_start"] < data["post_ts"] < data["window_end"]
    assert data["entry_ts"] < data["exit_ts"]


def test_meta_post_id_roundtrip():
    data = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    assert data["post_id"] == WINNER_ID


# ─────────────────────────────────────────────────────────────────────────────
# /api/orderflow/{post_id}/candles
# ─────────────────────────────────────────────────────────────────────────────

def test_candles_200():
    assert client.get(f"/api/orderflow/{WINNER_ID}/candles").status_code == 200


def test_candles_schema():
    data = client.get(f"/api/orderflow/{WINNER_ID}/candles").json()
    assert "bars" in data
    bars = data["bars"]
    assert len(bars) > 0
    for b in bars:
        for field in ("t", "o", "h", "l", "c"):
            assert field in b, f"candle bar missing field {field!r}"
        assert isinstance(b["t"], int)


def test_candles_ohlc_sanity():
    bars = client.get(f"/api/orderflow/{WINNER_ID}/candles").json()["bars"]
    for b in bars:
        if b["h"] is not None and b["l"] is not None:
            assert b["h"] >= b["l"], f"candle high < low at t={b['t']}"
        if b["o"] is not None and b["h"] is not None:
            assert b["h"] >= b["o"], f"candle high < open at t={b['t']}"
        if b["c"] is not None and b["l"] is not None:
            assert b["l"] <= b["c"], f"candle low > close at t={b['t']}"


def test_candles_30s_spacing():
    """Default 30s bars: consecutive timestamps should differ by exactly 30 000 ms."""
    bars = client.get(f"/api/orderflow/{WINNER_ID}/candles").json()["bars"]
    assert len(bars) >= 2, "Need at least 2 bars to check spacing"
    diffs = {bars[i + 1]["t"] - bars[i]["t"] for i in range(len(bars) - 1)}
    assert diffs == {30_000}, f"Expected 30s spacing, got {diffs}"


def test_candles_1m_param():
    bars = client.get(f"/api/orderflow/{WINNER_ID}/candles?bar=1m").json()["bars"]
    assert len(bars) > 0
    diffs = {bars[i + 1]["t"] - bars[i]["t"] for i in range(len(bars) - 1)}
    assert diffs == {60_000}, f"Expected 1-min spacing, got {diffs}"


# ─────────────────────────────────────────────────────────────────────────────
# /api/orderflow/{post_id}/footprint
# ─────────────────────────────────────────────────────────────────────────────

def test_footprint_200():
    assert client.get(f"/api/orderflow/{WINNER_ID}/footprint").status_code == 200


def test_footprint_schema():
    data = client.get(f"/api/orderflow/{WINNER_ID}/footprint").json()
    assert "bars" in data
    bars = data["bars"]
    assert len(bars) > 0
    for b in bars:
        for field in ("t", "cells", "delta", "cumDelta"):
            assert field in b, f"footprint bar missing {field!r}"
        assert isinstance(b["t"], int)
        assert isinstance(b["cells"], list)
        assert isinstance(b["delta"], int)
        assert isinstance(b["cumDelta"], int)


def test_footprint_cells_fields():
    data = client.get(f"/api/orderflow/{WINNER_ID}/footprint").json()
    bars_with_cells = [b for b in data["bars"] if b["cells"]]
    assert len(bars_with_cells) > 0, "No footprint bars have any cells"
    for b in bars_with_cells:
        for cell in b["cells"]:
            assert "price" in cell
            assert "bidVol" in cell
            assert "askVol" in cell
            assert isinstance(cell["bidVol"], int)
            assert isinstance(cell["askVol"], int)
            assert cell["bidVol"] >= 0
            assert cell["askVol"] >= 0


def test_footprint_delta_matches_cells():
    """Each bar's delta must equal sum(askVol - bidVol) across its cells."""
    bars = client.get(f"/api/orderflow/{WINNER_ID}/footprint").json()["bars"]
    for b in bars:
        cell_delta = sum(c["askVol"] - c["bidVol"] for c in b["cells"])
        assert b["delta"] == cell_delta, (
            f"t={b['t']}: bar delta={b['delta']} but cells sum to {cell_delta}"
        )


def test_footprint_cum_delta_is_running_sum():
    """cumDelta must equal running sum of per-bar deltas."""
    bars = client.get(f"/api/orderflow/{WINNER_ID}/footprint").json()["bars"]
    running = 0
    for b in bars:
        running += b["delta"]
        assert b["cumDelta"] == running, (
            f"t={b['t']}: cumDelta={b['cumDelta']} expected {running}"
        )


def test_footprint_cells_sorted_by_price():
    """Cells within each bar should be sorted ascending by price."""
    bars = client.get(f"/api/orderflow/{WINNER_ID}/footprint").json()["bars"]
    for b in bars:
        prices = [c["price"] for c in b["cells"]]
        assert prices == sorted(prices), f"t={b['t']}: cells not sorted by price"


# ─────────────────────────────────────────────────────────────────────────────
# Footprint delta-sign correctness (Task 1 — aggressor side mapping)
# ─────────────────────────────────────────────────────────────────────────────

def test_footprint_delta_sign_correct():
    """Verify buy-aggressor (side=B) → askVol (+delta) and sell-aggressor (side=A) → bidVol (-delta).

    Databento MBO convention confirmed empirically from the winner parquet:
    T.side='B' trades execute at the ask price (buy aggressor);
    T.side='A' trades execute at the bid price (sell aggressor).

    We locate the most B-dominant bar and the most A-dominant bar in the data
    and verify the API reports the correct delta sign for each.  Using
    dominance (rather than exclusivity) makes the test robust even when every
    bar contains a mix of both sides.
    """
    import pandas as pd

    from research._edgehunt.mbo_engine import load_mbo  # type: ignore[import]

    df = load_mbo(WINNER_ID)
    trades = df[df["action"] == "T"].copy()
    trades["bar_30s"] = trades["ts"].dt.floor("30s")

    b_vol = trades[trades["side"] == "B"].groupby("bar_30s")["size"].sum().rename("b")
    a_vol = trades[trades["side"] == "A"].groupby("bar_30s")["size"].sum().rename("a")
    # Cast to int64 before subtraction: the parquet size column is uint32, and
    # uint32 arithmetic (179 - 180) would underflow to 4 294 967 295 instead of -1.
    stats = pd.concat([b_vol, a_vol], axis=1).fillna(0).astype("int64")
    stats["imbalance"] = stats["b"] - stats["a"]

    bars_resp  = client.get(f"/api/orderflow/{WINNER_ID}/footprint").json()["bars"]
    bars_dict  = {b["t"]: b["delta"] for b in bars_resp}

    def _bar_ms(ts: "pd.Timestamp") -> int:
        return int(pd.Timestamp(ts).value // 1_000_000)

    # ── Most B-dominant bar → API delta must be positive ──────────────────────
    b_dom_ts = stats["imbalance"].idxmax()
    b_dom_ms = _bar_ms(b_dom_ts)
    b_imb    = int(stats.loc[b_dom_ts, "imbalance"])
    if b_dom_ms in bars_dict and b_imb > 0:
        assert bars_dict[b_dom_ms] > 0, (
            f"Bar {b_dom_ms}: B-dominant (net imbalance={b_imb:+,}) but API "
            f"delta={bars_dict[b_dom_ms]} — side='B' should map to askVol (+delta). "
            "Mapping may be inverted."
        )

    # ── Most A-dominant bar → API delta must be negative ─────────────────────
    a_dom_ts = stats["imbalance"].idxmin()
    a_dom_ms = _bar_ms(a_dom_ts)
    a_imb    = int(stats.loc[a_dom_ts, "imbalance"])
    if a_dom_ms in bars_dict and a_imb < 0:
        assert bars_dict[a_dom_ms] < 0, (
            f"Bar {a_dom_ms}: A-dominant (net imbalance={a_imb:+,}) but API "
            f"delta={bars_dict[a_dom_ms]} — side='A' should map to bidVol (-delta). "
            "Mapping may be inverted."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /api/orderflow/{post_id}/book
# ─────────────────────────────────────────────────────────────────────────────

def test_book_200():
    assert client.get(f"/api/orderflow/{WINNER_ID}/book").status_code == 200


def test_book_schema():
    data = client.get(f"/api/orderflow/{WINNER_ID}/book").json()
    assert "snapshots" in data
    snaps = data["snapshots"]
    assert len(snaps) > 0
    for s in snaps:
        for field in ("t", "bids", "asks"):
            assert field in s, f"book snapshot missing {field!r}"
        assert isinstance(s["t"], int)
        for side in (s["bids"], s["asks"]):
            for entry in side:
                assert "price" in entry and "size" in entry
                assert isinstance(entry["size"], int)
                assert entry["size"] > 0


def test_book_orientation():
    """In any snapshot with both sides populated, all bid prices < all ask prices."""
    snaps = client.get(f"/api/orderflow/{WINNER_ID}/book").json()["snapshots"]
    valid = [s for s in snaps if s["bids"] and s["asks"]]
    assert len(valid) > 0, "No book snapshots with both bids and asks populated"
    for s in valid:
        best_bid = max(b["price"] for b in s["bids"])
        best_ask = min(a["price"] for a in s["asks"])
        assert best_bid < best_ask, (
            f"t={s['t']}: best_bid={best_bid} >= best_ask={best_ask} (inverted book)"
        )


def test_book_bids_descending():
    """Bids should be sorted descending (best bid first = highest price first)."""
    snaps = client.get(f"/api/orderflow/{WINNER_ID}/book").json()["snapshots"]
    for s in [x for x in snaps if x["bids"]]:
        prices = [b["price"] for b in s["bids"]]
        assert prices == sorted(prices, reverse=True), (
            f"t={s['t']}: bids not sorted descending"
        )


def test_book_asks_ascending():
    """Asks should be sorted ascending (best ask first = lowest price first)."""
    snaps = client.get(f"/api/orderflow/{WINNER_ID}/book").json()["snapshots"]
    for s in [x for x in snaps if x["asks"]]:
        prices = [a["price"] for a in s["asks"]]
        assert prices == sorted(prices), (
            f"t={s['t']}: asks not sorted ascending"
        )


def test_book_depth_capped():
    """Each snapshot must have at most BOOK_DEPTH bids and BOOK_DEPTH asks."""
    snaps = client.get(f"/api/orderflow/{WINNER_ID}/book").json()["snapshots"]
    for s in snaps:
        assert len(s["bids"]) <= BOOK_DEPTH, f"t={s['t']}: too many bids"
        assert len(s["asks"]) <= BOOK_DEPTH, f"t={s['t']}: too many asks"


def test_book_res_1s_finer_than_default():
    """res=1s must yield significantly more snapshots than the 30s default over
    the same time window, allowing the DOM to update smoothly while scrubbing.

    We scope both requests to a 5-minute sub-window (from/to) so the test
    completes quickly while still exercising the new parameters.
    """
    meta = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    mid = (meta["window_start"] + meta["window_end"]) // 2
    from_ms = mid - 150_000   # 2.5 min before mid
    to_ms   = mid + 150_000   # 2.5 min after mid

    default_snaps = client.get(
        f"/api/orderflow/{WINNER_ID}/book?from={from_ms}&to={to_ms}"
    ).json()["snapshots"]

    fine_snaps = client.get(
        f"/api/orderflow/{WINNER_ID}/book?res=1s&from={from_ms}&to={to_ms}"
    ).json()["snapshots"]

    assert len(fine_snaps) > len(default_snaps), (
        f"res=1s produced {len(fine_snaps)} snapshots but default 30s produced "
        f"{len(default_snaps)} — finer resolution should yield more snapshots."
    )
    # 5-minute window at 1 s -> expect at least 200 snapshots (some may be boundary-edge)
    assert len(fine_snaps) >= 200, (
        f"Expected ~300 snapshots for 5-min/1s window, got {len(fine_snaps)}"
    )


def test_book_res_1s_spacing():
    """Consecutive 1-s snapshots should be spaced ~1000 ms apart."""
    meta = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    mid = (meta["window_start"] + meta["window_end"]) // 2
    from_ms = mid - 30_000   # 30-second window for speed
    to_ms   = mid + 30_000

    snaps = client.get(
        f"/api/orderflow/{WINNER_ID}/book?res=1s&from={from_ms}&to={to_ms}"
    ).json()["snapshots"]
    assert len(snaps) >= 2, "Need at least 2 snapshots to check spacing"
    diffs = {snaps[i + 1]["t"] - snaps[i]["t"] for i in range(len(snaps) - 1)}
    assert diffs == {1_000}, f"Expected 1-second (1000 ms) spacing, got {diffs}"


def test_book_from_to_restricts_snapshots():
    """from/to params must restrict the returned snapshots to within that window."""
    meta = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    mid = (meta["window_start"] + meta["window_end"]) // 2
    from_ms = mid - 60_000
    to_ms   = mid + 60_000

    all_snaps = client.get(f"/api/orderflow/{WINNER_ID}/book").json()["snapshots"]
    windowed  = client.get(
        f"/api/orderflow/{WINNER_ID}/book?from={from_ms}&to={to_ms}"
    ).json()["snapshots"]

    assert len(windowed) < len(all_snaps), "Windowed query should return fewer snapshots"
    for s in windowed:
        assert from_ms <= s["t"] <= to_ms, (
            f"Snapshot t={s['t']} outside requested window [{from_ms}, {to_ms}]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# /api/orderflow/{post_id}/trades
# ─────────────────────────────────────────────────────────────────────────────

def test_trades_200():
    assert client.get(f"/api/orderflow/{WINNER_ID}/trades").status_code == 200


def test_trades_schema():
    data = client.get(f"/api/orderflow/{WINNER_ID}/trades").json()
    assert "trades" in data
    trades = data["trades"]
    assert len(trades) > 0
    for t in trades:
        for field in ("t", "price", "size", "side"):
            assert field in t, f"trade missing field {field!r}"
        assert isinstance(t["t"], int)
        assert isinstance(t["size"], int)
        assert t["size"] > 0
        assert t["side"] in {"A", "B"}


def test_trades_trade_only_sorted():
    """Trades endpoint must return only T (trade-print) events, sorted by t."""
    import pandas as pd

    from research._edgehunt.mbo_engine import load_mbo  # type: ignore[import]

    meta = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    trades = client.get(f"/api/orderflow/{WINNER_ID}/trades").json()["trades"]
    times = [t["t"] for t in trades]
    assert times == sorted(times), "Trades are not in chronological order"

    df = load_mbo(WINNER_ID)
    w_start = pd.Timestamp(meta["window_start"], unit="ms", tz="UTC")
    w_end = pd.Timestamp(meta["window_end"], unit="ms", tz="UTC")
    expected = len(
        df[(df["action"] == "T") & (df["ts"] >= w_start) & (df["ts"] <= w_end)]
    )
    assert len(trades) == expected, (
        f"trades count {len(trades)} != expected T events {expected}"
    )


def test_trades_from_to_filter():
    """from/to epoch-ms params must restrict trades to the requested time range."""
    meta = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    all_trades = client.get(f"/api/orderflow/{WINNER_ID}/trades").json()["trades"]
    assert len(all_trades) >= 10, "Need enough trades to exercise from/to filter"

    mid = len(all_trades) // 2
    from_ms = all_trades[mid]["t"]
    to_ms = all_trades[-1]["t"]

    filtered = client.get(
        f"/api/orderflow/{WINNER_ID}/trades?from={from_ms}&to={to_ms}"
    ).json()["trades"]

    assert len(filtered) > 0
    for t in filtered:
        assert t["t"] >= from_ms, f"trade t={t['t']} < from={from_ms}"
        assert t["t"] <= to_ms, f"trade t={t['t']} > to={to_ms}"


def test_trades_within_window():
    """Default trades must fall within the call window bounds."""
    meta = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    trades = client.get(f"/api/orderflow/{WINNER_ID}/trades").json()["trades"]
    for t in trades:
        assert meta["window_start"] <= t["t"] <= meta["window_end"], (
            f"trade t={t['t']} outside window "
            f"[{meta['window_start']}, {meta['window_end']}]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# /api/orderflow/{post_id}/tape
# ─────────────────────────────────────────────────────────────────────────────

def test_tape_200():
    assert client.get(f"/api/orderflow/{WINNER_ID}/tape").status_code == 200


def test_tape_schema():
    data = client.get(f"/api/orderflow/{WINNER_ID}/tape").json()
    assert "events" in data
    events = data["events"]
    assert len(events) > 0
    for e in events:
        for field in ("t", "action", "side", "price", "size", "order_id"):
            assert field in e, f"tape event missing {field!r}"
        assert isinstance(e["t"], int)
        assert isinstance(e["size"], int)
        assert isinstance(e["order_id"], int)
        assert e["size"] > 0


def test_tape_time_ordered():
    events = client.get(f"/api/orderflow/{WINNER_ID}/tape").json()["events"]
    times = [e["t"] for e in events]
    assert times == sorted(times), "Tape events are not in chronological order"


def test_tape_limit_param():
    data = client.get(f"/api/orderflow/{WINNER_ID}/tape?limit=10").json()
    assert len(data["events"]) <= 10


def test_tape_from_to_filter():
    """from/to epoch-ms params must restrict events to the requested time range."""
    events = client.get(f"/api/orderflow/{WINNER_ID}/tape?limit=200").json()["events"]
    if len(events) < 10:
        pytest.skip("Not enough tape events to exercise from/to filter")

    mid = len(events) // 2
    from_ms = events[mid]["t"]
    to_ms   = events[-1]["t"]

    filtered = client.get(
        f"/api/orderflow/{WINNER_ID}/tape?from={from_ms}&to={to_ms}&limit=1000"
    ).json()["events"]

    assert len(filtered) > 0
    for e in filtered:
        assert e["t"] >= from_ms, f"event t={e['t']} < from={from_ms}"
        assert e["t"] <= to_ms,   f"event t={e['t']} > to={to_ms}"


def test_tape_actions_are_valid():
    """All action codes should be known Databento MBO action types."""
    events = client.get(f"/api/orderflow/{WINNER_ID}/tape").json()["events"]
    known = {"A", "C", "M", "T", "F"}
    for e in events:
        assert e["action"] in known, f"Unknown tape action {e['action']!r}"


def test_tape_sides_are_valid():
    events = client.get(f"/api/orderflow/{WINNER_ID}/tape").json()["events"]
    for e in events:
        assert e["side"] in {"A", "B", "N"}, f"Unknown side {e['side']!r}"


def test_tape_from_to_spread_across_window():
    """from/to must return events SPREAD across the requested window, not all at start.

    Requests a sub-window that starts well AFTER the window start, verifying
    that events genuinely originate from the specified range rather than being
    returned from the beginning of the data.
    """
    meta = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    # Use the last quarter of the window to ensure we're NOT at the start
    w_start = meta["window_start"]
    w_end   = meta["window_end"]
    quarter  = (w_end - w_start) // 4
    from_ms  = w_start + 3 * quarter   # last 25% of the window
    to_ms    = w_end

    events = client.get(
        f"/api/orderflow/{WINNER_ID}/tape?from={from_ms}&to={to_ms}&limit=200"
    ).json()["events"]

    assert len(events) > 0, "No events returned for last-quarter window"
    for e in events:
        assert e["t"] >= from_ms, f"Event t={e['t']} is before from_ms={from_ms}"
        assert e["t"] <= to_ms,   f"Event t={e['t']} is after to_ms={to_ms}"

    # Events must NOT all cluster at the same millisecond — they are spread out
    unique_times = len({e["t"] for e in events})
    assert unique_times > 1, (
        "All tape events share the same timestamp — from/to filter may not be working"
    )


def test_tape_default_no_from_to_covers_full_window():
    """Without from/to the tape returns events from the window start (capped at limit)."""
    meta   = client.get(f"/api/orderflow/{WINNER_ID}/meta").json()
    events = client.get(f"/api/orderflow/{WINNER_ID}/tape?limit=10").json()["events"]
    assert len(events) > 0
    # First event must be at or after window_start
    assert events[0]["t"] >= meta["window_start"], (
        f"First tape event t={events[0]['t']} is before window_start={meta['window_start']}"
    )
