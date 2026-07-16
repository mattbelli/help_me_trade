#!/usr/bin/env python3
"""MBO order-flow engine — resting-book liquidity heatmap + footprint.

Reads local MBO parquet files (already downloaded from Databento):
    data/research/_edgehunt/orderflow/<post_id>_mbo.parquet

Builds TWO panels per call:
  TOP:    Liquidity heatmap (resting book size, color intensity per cell)
          + 1-min OHLC candle overlay.  Large resting bids = blue-bright;
          ask liquidity = red-bright.  Reveals icebergs / absorption.
  BOTTOM: Per-bar net delta bars (green=buy, red=sell) + cumulative-delta
          step line.

X-window: post_ts − 15 min  →  exit_ts + 5 min  (clamped to MBO data range)
Y-window: min(level, exit_px) − 6 pts  →  max(level, exit_px) + 6 pts
DPI:  200  (mobile-legible).

Vertical lines in render:
  WHITE dotted : post_ts   (Adam's call / tweet)
  GOLD  dashed : entry_ts  (fill)
  CYAN  dashed : exit_ts   (exit / P&L realised)

All displayed timestamps in US/Eastern (ET).

Databento MBO conventions (verified from data):
  - price field is ALREADY in float (not fixed-point int).
    Range ~5127–5168 for Apr-2025 /ES — confirmed sane.
  - action codes present: A=add, C=cancel, M=modify, T=trade, F=fill.
    (No R/clear seen in these files.)
  - side on A/C/M events: B = bid (buy limit order, demand below mid);
                           A = ask (sell limit order, supply above mid).
    Verified: at 19:05 UTC the dominant side='B' concentration is at/below
    mid price, and dominant side='A' concentration is above mid — correct.
  - side on T events: B = buy aggressor (hit ask) → ask_vol (+delta);
                      A = sell aggressor (hit bid) → bid_vol (−delta).
  - F events are per-order fills (2× T count); T is the trade print.
  - flags 130 (0x82) dominate; no explicit snapshot-only batch at start.
    We therefore process all A/C/M events from ts=0 to build the book.

Run from repo root:
    PYTHONIOENCODING=utf-8 python -m research._edgehunt.mbo_engine
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.colors import LogNorm, TwoSlopeNorm

# ── Project root ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────
CSV_PATH  = ROOT / "data" / "research" / "adam_trigger_study.csv"
MBO_DIR   = ROOT / "data" / "research" / "_edgehunt" / "orderflow"
OUT_DIR   = ROOT / "data" / "research" / "_edgehunt"

ES_TICK       = 0.25   # /ES minimum price increment
BAR_MINUTES   = 1      # footprint / heatmap time resolution
PRE_MINUTES   = 15     # lead-in before post_ts
POST_MINUTES  = 5      # buffer after exit_ts
ZOOM_MARGIN   = 6.0    # pts margin around min(level,exit_px)..max(level,exit_px)
DPI           = 200

# Color palette
BG    = "#0d1117"
GOLD  = "#FFD700"
WHITE = "#FFFFFF"
CYAN  = "#00CFFF"
GREEN = "#26a641"
RED   = "#da3633"
GREY  = "#555555"
LIGHT = "#aaaaaa"

# US/Eastern timezone (handles both EST and EDT automatically)
TZ_ET = "America/New_York"

# Preferred winner post_id (Adam's famous Apr-21 long that hit target +19 pts)
PREFERRED_WINNER_ID = "1914389641555558635"


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  CALL SELECTION
# ═══════════════════════════════════════════════════════════════════════════════

def select_calls(csv_path: Path, mbo_dir: Path) -> list[dict]:
    """Return [winner, loser, dud] for calls that have MBO parquet files."""
    df = pd.read_csv(csv_path)
    df["_has_pq"] = df["post_id"].apply(
        lambda pid: (mbo_dir / f"{int(pid)}_mbo.parquet").exists()
    )
    df = df[df["_has_pq"]].copy()
    df["points_net"] = pd.to_numeric(df["points_net"], errors="coerce")
    df = df.dropna(subset=["points_net"])

    # Force preferred winner if available
    pref_mask = df["post_id"].astype(str) == PREFERRED_WINNER_ID
    winner_idx = pref_mask.idxmax() if pref_mask.any() else df["points_net"].idxmax()
    loser_idx  = df["points_net"].idxmin()

    rest = df[~df.index.isin({winner_idx, loser_idx})]
    dud_idx = (rest["points_net"] - 0).abs().idxmin()

    calls: list[dict] = []
    for label, idx in [("WINNER", winner_idx), ("LOSER", loser_idx), ("DUD", dud_idx)]:
        row = df.loc[idx]

        def _ts(col: str) -> pd.Timestamp:
            val = row.get(col, None)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return None
            ts = pd.Timestamp(val)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return ts

        calls.append({
            "label":      label,
            "post_id":    str(int(row["post_id"])),
            "post_ts":    _ts("post_ts"),
            "entry_ts":   _ts("entry_ts"),
            "exit_ts":    _ts("exit_ts"),
            "direction":  str(row["direction"]),
            "level":      float(row["level"]),
            "exit_px":    float(row["exit_px"]) if "exit_px" in row.index else float("nan"),
            "points_net": float(row["points_net"]),
            "exit_reason": str(row.get("exit_reason", "")),
        })
    return calls


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_mbo(post_id: str) -> pd.DataFrame:
    """Load MBO parquet; normalize ts cols; filter extreme price outliers."""
    pq = MBO_DIR / f"{post_id}_mbo.parquet"
    df = pd.read_parquet(pq)

    for col in ("ts", "ts_event"):
        if col in df.columns:
            if not pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = pd.to_datetime(df[col], utc=True)
            elif df[col].dt.tz is None:
                df[col] = df[col].dt.tz_localize("UTC")

    # Filter price outliers (artifact orders far outside any reasonable /ES range)
    df = df[(df["price"] >= 3000) & (df["price"] <= 9000)].copy()
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  ZOOM GRID
# ═══════════════════════════════════════════════════════════════════════════════

def make_zoom_grid(
    post_ts: pd.Timestamp,
    entry_ts: pd.Timestamp | None,
    exit_ts: pd.Timestamp | None,
    level: float,
    exit_px: float,
    data_start: pd.Timestamp | None = None,
    data_end: pd.Timestamp | None = None,
) -> tuple[list, list]:
    """Return (bar_times, price_levels) for the trade-spanning zoom window.

    X-window: post_ts − PRE_MINUTES  →  exit_ts + POST_MINUTES
              (clamped to MBO data range so we never request missing bars)
    Y-range:  min(level, exit_px) − ZOOM_MARGIN  →  max(level, exit_px) + ZOOM_MARGIN
    """
    # ── X window ─────────────────────────────────────────────────────────────
    window_start = post_ts - pd.Timedelta(minutes=PRE_MINUTES)
    # Use exit_ts if available; fall back to post_ts + 30 min
    window_end_base = exit_ts if exit_ts is not None else (post_ts + pd.Timedelta(minutes=30))
    window_end = window_end_base + pd.Timedelta(minutes=POST_MINUTES)

    # Clamp to data availability
    if data_start is not None:
        window_start = max(window_start, data_start.floor(f"{BAR_MINUTES}min"))
    if data_end is not None:
        window_end = min(window_end, data_end.ceil(f"{BAR_MINUTES}min"))

    start = window_start.floor(f"{BAR_MINUTES}min")
    end   = window_end.ceil(f"{BAR_MINUTES}min")
    bar_times = list(pd.date_range(start, end, freq=f"{BAR_MINUTES}min"))

    # ── Y range (covers both the level and the exit price) ───────────────────
    px_lo = min(level, exit_px) if not np.isnan(exit_px) else level
    px_hi = max(level, exit_px) if not np.isnan(exit_px) else level
    lo = round((px_lo - ZOOM_MARGIN) / ES_TICK) * ES_TICK
    hi = round((px_hi + ZOOM_MARGIN) / ES_TICK) * ES_TICK
    n  = int(round((hi - lo) / ES_TICK)) + 1
    price_levels = [round(lo + i * ES_TICK, 2) for i in range(n)]
    return bar_times, price_levels


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  TRADE EXTRACTION  →  footprint
# ═══════════════════════════════════════════════════════════════════════════════

def extract_trades(df: pd.DataFrame, bar_times: list) -> pd.DataFrame:
    """Build footprint from T (trade-print) events.

    Aggressor convention (verified: T events only, no F):
      side='B'  buy aggressor, hit the ask  → ask_vol  (+delta)
      side='A'  sell aggressor, hit the bid → bid_vol  (−delta)

    Returns DataFrame: bar, price_tick, bid_vol, ask_vol, delta, total
    """
    trades = df[df["action"] == "T"].copy()
    if trades.empty:
        return pd.DataFrame(columns=["bar", "price_tick", "bid_vol", "ask_vol", "delta", "total"])

    trades["price_tick"] = (trades["price"] / ES_TICK).round() * ES_TICK
    trades["bar"] = trades["ts"].dt.floor(f"{BAR_MINUTES}min")

    ask_grp = (
        trades[trades["side"] == "B"]
        .groupby(["bar", "price_tick"])["size"].sum()
        .rename("ask_vol")
    )
    bid_grp = (
        trades[trades["side"] == "A"]
        .groupby(["bar", "price_tick"])["size"].sum()
        .rename("bid_vol")
    )

    fp = (
        pd.concat([bid_grp, ask_grp], axis=1)
        .fillna(0).astype(int).reset_index()
    )
    fp["delta"] = fp["ask_vol"] - fp["bid_vol"]
    fp["total"] = fp["ask_vol"] + fp["bid_vol"]
    return fp


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  BOOK RECONSTRUCTION  →  resting-book snapshots
# ═══════════════════════════════════════════════════════════════════════════════

def reconstruct_book(df: pd.DataFrame, bar_times: list) -> pd.DataFrame:
    """Stream A/C/M/F events to maintain order book; snapshot at each bar boundary.

    Side convention (verified against Databento CME MBO data):
      A/C/M/F side='B' → bid (buy limit order, resting demand below mid)
      A/C/M/F side='A' → ask (sell limit order, resting supply above mid)

    F (fill) event handling:
      In Databento CME MBO data, F events represent fills of resting orders.
      F.size = fill quantity (amount consumed from the resting order).
      We decrement the resting order's tracked size by F.size; at size ≤ 0 we
      remove it.  This is correct for REAL limit orders (fills consumed liquidity
      that otherwise stays in the book until an explicit C or M arrives).

      Safety: if a C event arrives after an F (full-fill cancel), book.pop is
      a no-op.  If an M event follows a partial F, M.size is the correct
      remaining size and overwrites our decremented value to the same result.

    CROSSING ROOT CAUSE (do NOT remove the de-cross in api/orderflow_routes.py):
      Verified against the winner parquet: the raw book is PERMANENTLY crossed
      (100% of snapshots have best_bid >= best_ask) even with F handling applied.
      Investigation shows the cause is CME IMPLIED / SYNTHETIC SPREAD ORDERS —
      not missed fills.  All crossed orders carry flags=0x82 (F_LAST|F_BATCH),
      the same marker as regular orders; there is no reliable flag to distinguish
      them from the MBO feed without the CME FIX field "OrigType" (not available
      here).  These synthetic legs get added and modified continuously at prices
      far from the current NBBO, creating a permanently crossed raw book.

      F handling is still correct and safe for real limit orders, but it does
      NOT reduce the synthetic-order crossings.  The de-cross in
      api/orderflow_routes.py (split at midpoint when best_bid >= best_ask)
      remains the only guard that produces a usable DOM ladder and heatmap.

    Returns DataFrame: bar, price_tick, bid_size, ask_size
    """
    book_events = df[df["action"].isin(["A", "C", "M", "F"])].copy()
    book_events = book_events.sort_values("ts").reset_index(drop=True)

    book: dict[int, tuple[float, str, int]] = {}   # order_id → (price, side, size)
    bar_times_sorted = sorted(bar_times)
    bar_idx = 0
    n_bars  = len(bar_times_sorted)

    # Snapshot helper: aggregate current book into {(price, side): size}
    def take_snapshot(bar_ts: pd.Timestamp) -> tuple:
        levels: dict[tuple[float, str], int] = {}
        for order_id, (price, side, size) in book.items():
            key = (price, side)
            levels[key] = levels.get(key, 0) + size
        return bar_ts, levels

    snapshots: list[tuple] = []

    total_rows = len(book_events)
    print(f"    Book reconstruction: {total_rows:,} A/C/M events ...", end="", flush=True)

    for i, row in enumerate(book_events.itertuples(index=False)):
        ts        = row.ts
        action    = row.action
        order_id  = row.order_id

        # Flush all bar boundaries that have passed
        while bar_idx < n_bars and bar_times_sorted[bar_idx] <= ts:
            snapshots.append(take_snapshot(bar_times_sorted[bar_idx]))
            bar_idx += 1

        # Update book
        if action == "A":
            book[order_id] = (round(row.price / ES_TICK) * ES_TICK, row.side, int(row.size))
        elif action == "C":
            book.pop(order_id, None)
        elif action == "M":
            book[order_id] = (round(row.price / ES_TICK) * ES_TICK, row.side, int(row.size))
        elif action == "F":
            # Decrement resting size by fill quantity; remove if fully consumed.
            # F.size = fill quantity, NOT remaining size (that's what M.size carries).
            if order_id in book:
                price_f, side_f, remaining = book[order_id]
                new_size = remaining - int(row.size)
                if new_size > 0:
                    book[order_id] = (price_f, side_f, new_size)
                else:
                    del book[order_id]

    # Remaining bar snapshots
    while bar_idx < n_bars:
        snapshots.append(take_snapshot(bar_times_sorted[bar_idx]))
        bar_idx += 1

    print(f" done ({len(snapshots)} snapshots)")

    if not snapshots:
        return pd.DataFrame(columns=["bar", "price_tick", "bid_size", "ask_size"])

    # Flatten snapshots to records
    records = []
    for bar_ts, levels in snapshots:
        for (price, side), size in levels.items():
            records.append({"bar": bar_ts, "price_tick": price, "side": side, "size": size})

    if not records:
        return pd.DataFrame(columns=["bar", "price_tick", "bid_size", "ask_size"])

    recs = pd.DataFrame(records)
    bid_df = (
        recs[recs["side"] == "B"]        # B = bid (demand, below mid)
        .groupby(["bar", "price_tick"])["size"].sum()
        .rename("bid_size")
    )
    ask_df = (
        recs[recs["side"] == "A"]        # A = ask (supply, above mid)
        .groupby(["bar", "price_tick"])["size"].sum()
        .rename("ask_size")
    )
    book_df = pd.concat([bid_df, ask_df], axis=1).fillna(0).astype(int).reset_index()
    return book_df


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  OHLC CANDLES
# ═══════════════════════════════════════════════════════════════════════════════

def build_candles(df: pd.DataFrame, bar_times: list) -> pd.DataFrame:
    """Build 1-min OHLC from T events; reindex to all bar_times."""
    trades = df[df["action"] == "T"].copy()
    if trades.empty:
        bar_df = pd.DataFrame({"bar": bar_times})
        bar_df[["open", "high", "low", "close", "vol"]] = float("nan")
        return bar_df

    trades["bar"] = trades["ts"].dt.floor(f"{BAR_MINUTES}min")
    ohlc = (
        trades.groupby("bar")["price"]
        .agg(open="first", high="max", low="min", close="last")
        .reset_index()
    )
    vol = trades.groupby("bar")["size"].sum().rename("vol").reset_index()
    ohlc = ohlc.merge(vol, on="bar", how="left")

    bar_df = pd.DataFrame({"bar": bar_times})
    ohlc   = bar_df.merge(ohlc, on="bar", how="left")
    # Forward-fill close to fill any gap bars
    ohlc["close"] = ohlc["close"].ffill()
    ohlc[["open", "high", "low"]] = ohlc[["open", "high", "low"]].ffill()
    return ohlc


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  ABSORPTION READ
# ═══════════════════════════════════════════════════════════════════════════════

def compute_absorption_read(
    fp: pd.DataFrame,
    book_df: pd.DataFrame,
    call: dict,
    bar_times: list,
    call_bar_idx: int,
) -> str:
    """One-line automated read of order-flow at Adam's level around the call."""
    level     = call["level"]
    direction = call["direction"]

    # Window: 3 bars before call + 2 bars after
    win_bars = bar_times[max(0, call_bar_idx - 3) : call_bar_idx + 3]
    if not win_bars:
        return "insufficient data"

    # Resting book at Adam's level (±1 tick)
    lvl_book = book_df[
        book_df["bar"].isin(win_bars)
        & book_df["price_tick"].between(level - ES_TICK, level + ES_TICK)
    ]
    total_bid_rest = int(lvl_book["bid_size"].sum())
    total_ask_rest = int(lvl_book["ask_size"].sum())

    # Footprint at Adam's level (±2 ticks)
    fp_win = fp[
        fp["bar"].isin(win_bars)
        & fp["price_tick"].between(level - ES_TICK * 2, level + ES_TICK * 2)
    ]
    net_delta  = int(fp_win["delta"].sum())
    total_vol  = int(fp_win["total"].sum())

    if total_vol == 0:
        return (
            f"no trades printed at level "
            f"(resting bid={total_bid_rest:,}, ask={total_ask_rest:,}) "
            f"-> inactive zone"
        )

    # Build read
    if direction == "long":
        if total_bid_rest > 200 and net_delta < -int(total_vol * 0.15):
            return (
                f"net delta {net_delta:+,} (sell flow), "
                f"resting bid {total_bid_rest:,} held -> BID ABSORPTION (demand zone)"
            )
        if total_bid_rest > 100 and abs(net_delta) < int(total_vol * 0.1):
            return (
                f"resting bid {total_bid_rest:,} at level, "
                f"balanced delta {net_delta:+,} vol={total_vol:,} -> SUPPORT HOLD"
            )
        if net_delta > int(total_vol * 0.2):
            return (
                f"net delta {net_delta:+,} (buy pressure), "
                f"resting ask {total_ask_rest:,} -> BREAKOUT FLOW"
            )
        return (
            f"net delta {net_delta:+,}, vol {total_vol:,}, "
            f"resting bid={total_bid_rest:,} ask={total_ask_rest:,} -> mixed"
        )
    else:
        if total_ask_rest > 200 and net_delta > int(total_vol * 0.15):
            return (
                f"net delta {net_delta:+,} (buy flow), "
                f"resting ask {total_ask_rest:,} held -> ASK ABSORPTION (supply zone)"
            )
        if total_ask_rest > 100 and abs(net_delta) < int(total_vol * 0.1):
            return (
                f"resting ask {total_ask_rest:,} at level, "
                f"balanced delta {net_delta:+,} vol={total_vol:,} -> RESISTANCE HOLD"
            )
        if net_delta < -int(total_vol * 0.2):
            return (
                f"net delta {net_delta:+,} (sell pressure), "
                f"resting bid {total_bid_rest:,} -> BREAKDOWN FLOW"
            )
        return (
            f"net delta {net_delta:+,}, vol {total_vol:,}, "
            f"resting ask={total_ask_rest:,} bid={total_bid_rest:,} -> mixed"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  RENDER  —  mobile-legible 2-panel PNG
# ═══════════════════════════════════════════════════════════════════════════════

def _ts_to_bar_idx(ts: pd.Timestamp | None, bar_times: list) -> int | None:
    """Find the bar index closest to `ts` in bar_times, or None."""
    if ts is None:
        return None
    bar_floor = ts.floor(f"{BAR_MINUTES}min")
    if bar_floor in bar_times:
        return bar_times.index(bar_floor)
    diffs = [abs((b - ts).total_seconds()) for b in bar_times]
    return int(np.argmin(diffs))


def _et(ts: pd.Timestamp | None) -> pd.Timestamp | None:
    """Convert a UTC Timestamp to US/Eastern."""
    if ts is None:
        return None
    return ts.tz_convert(TZ_ET)


def _fmt_et(ts: pd.Timestamp | None) -> str:
    """Format a US/Eastern timestamp as '2:43 PM ET' (cross-platform)."""
    if ts is None:
        return "?"
    # %I gives zero-padded hour; lstrip removes leading zero
    return ts.strftime("%I:%M %p ET").lstrip("0")


def render_png(
    fp: pd.DataFrame,
    book_df: pd.DataFrame,
    candles: pd.DataFrame,
    call: dict,
    bar_times: list,
    price_levels: list,
    absorption_read: str,
    out_path: Path,
) -> None:
    """Render two-panel chart:
      Top  : Liquidity heatmap (resting book) + OHLC candle overlay
      Bottom: Per-bar delta bars + cumulative delta line

    All timestamps displayed in US/Eastern (ET).
    Three vertical lines: post_ts (white dotted), entry_ts (gold dashed),
                          exit_ts (cyan dashed).
    """
    post_ts   = call["post_ts"]
    entry_ts  = call.get("entry_ts")
    exit_ts   = call.get("exit_ts")
    level     = call["level"]
    exit_px   = call.get("exit_px", float("nan"))
    n_bars    = len(bar_times)
    n_price   = len(price_levels)

    # ── ET equivalents for display ─────────────────────────────────────────────
    post_et   = _et(post_ts)
    entry_et  = _et(entry_ts)
    exit_et   = _et(exit_ts)

    # ── Build heatmap matrices (price x bar) ───────────────────────────────────
    book_zoom = book_df[
        book_df["bar"].isin(bar_times)
        & book_df["price_tick"].isin(price_levels)
    ].copy()

    price_rows_desc = price_levels[::-1]   # HIGH → LOW (top of chart = highest price)

    # pivot: index=price (desc), columns=bar
    bid_hm = (
        book_zoom.pivot_table(index="price_tick", columns="bar", values="bid_size", fill_value=0)
        .reindex(index=price_rows_desc, columns=bar_times, fill_value=0)
    )  # shape (n_price, n_bars), rows sorted HIGH→LOW
    ask_hm = (
        book_zoom.pivot_table(index="price_tick", columns="bar", values="ask_size", fill_value=0)
        .reindex(index=price_rows_desc, columns=bar_times, fill_value=0)
    )
    total_hm = bid_hm.values + ask_hm.values

    # net resting imbalance: +ve = more bids (demand/green), -ve = more asks (supply/red)
    imbalance_hm = bid_hm.values.astype(float) - ask_hm.values.astype(float)

    # ── Build footprint matrices ────────────────────────────────────────────────
    fp_zoom = fp[
        fp["bar"].isin(bar_times)
        & fp["price_tick"].isin(price_levels)
    ].copy()

    ask_fp = (
        fp_zoom.pivot_table(index="price_tick", columns="bar", values="ask_vol", fill_value=0)
        .reindex(index=price_rows_desc, columns=bar_times, fill_value=0)
    )
    bid_fp = (
        fp_zoom.pivot_table(index="price_tick", columns="bar", values="bid_vol", fill_value=0)
        .reindex(index=price_rows_desc, columns=bar_times, fill_value=0)
    )

    # ── Per-bar and cumulative delta ────────────────────────────────────────────
    bar_delta = (
        fp[fp["bar"].isin(bar_times)]
        .groupby("bar")["delta"].sum()
        .reindex(bar_times, fill_value=0)
    )
    cum_delta = bar_delta.cumsum()

    # ── Vertical line bar indices ──────────────────────────────────────────────
    post_j  = _ts_to_bar_idx(post_ts,  bar_times)
    entry_j = _ts_to_bar_idx(entry_ts, bar_times)
    exit_j  = _ts_to_bar_idx(exit_ts,  bar_times)

    # ── Level row index ────────────────────────────────────────────────────────
    level_i = next(
        (i for i, p in enumerate(price_rows_desc) if abs(p - level) < ES_TICK / 2),
        None,
    )

    # ── Figure layout ──────────────────────────────────────────────────────────
    # Each bar cell ~0.30" wide; each price cell ~0.15" tall
    fig_w = max(16.0, n_bars * 0.30 + 3.0)
    fig_h = max(10.0, n_price * 0.15 + 5.0)  # extra vertical space for titles

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BG, dpi=DPI)

    # Reserve 10% at top for titles so suptitle and subtitle never overlap axes
    fig.subplots_adjust(top=0.89, bottom=0.07, left=0.07, right=0.96)

    gs  = gridspec.GridSpec(
        3, 2,
        figure=fig,
        height_ratios=[6, 0.08, 2],   # top=heatmap, gap, bottom=delta
        width_ratios=[30, 1],
        hspace=0.04,
        wspace=0.02,
    )
    ax_top   = fig.add_subplot(gs[0, 0])
    ax_cb    = fig.add_subplot(gs[0, 1])
    ax_bot   = fig.add_subplot(gs[2, 0], sharex=ax_top)

    for ax in (ax_top, ax_cb, ax_bot):
        ax.set_facecolor(BG)

    # ═══════════════════════════════════════════════════════════════════════════
    # TOP PANEL — Liquidity Heatmap + Candles
    # ═══════════════════════════════════════════════════════════════════════════

    # --- Heatmap: total resting size (log scale for visibility) ----------------
    hm = total_hm.astype(float)
    hm[hm == 0] = float("nan")

    max_rest = float(np.nanmax(hm)) if not np.all(np.isnan(hm)) else 1.0
    if max_rest < 1:
        max_rest = 1.0
    norm_hm = LogNorm(vmin=1, vmax=max(max_rest, 2))

    im = ax_top.imshow(
        hm,
        aspect="auto",
        cmap="YlOrRd",        # yellow→orange→red: more liquidity = hotter
        norm=norm_hm,
        origin="upper",
        extent=[-0.5, n_bars - 0.5, n_price - 0.5, -0.5],
        alpha=0.75,
        interpolation="nearest",
        zorder=1,
    )

    # Colorbar
    cb = fig.colorbar(im, cax=ax_cb, label="Resting size (log)")
    cb.ax.yaxis.set_tick_params(color=LIGHT, labelcolor=LIGHT, labelsize=8)
    cb.set_label("Resting size (log)", color=LIGHT, fontsize=9)

    # --- Bid/Ask imbalance overlay: green = bids heavy, red = asks heavy -------
    imb = imbalance_hm.copy().astype(float)
    imb_max = float(np.abs(imb).max()) if imb.size > 0 else 1.0
    if imb_max < 1:
        imb_max = 1.0
    norm_imb = TwoSlopeNorm(vcenter=0, vmin=-imb_max, vmax=imb_max)

    ax_top.imshow(
        imb,
        aspect="auto",
        cmap="RdYlGn",         # green = bid-heavy (demand), red = ask-heavy (supply)
        norm=norm_imb,
        origin="upper",
        extent=[-0.5, n_bars - 0.5, n_price - 0.5, -0.5],
        alpha=0.35,
        interpolation="nearest",
        zorder=2,
    )

    # --- OHLC Candles -----------------------------------------------------------
    candles_zoom = candles[candles["bar"].isin(bar_times)].copy()
    for j, bar_ts in enumerate(bar_times):
        row = candles_zoom[candles_zoom["bar"] == bar_ts]
        if row.empty:
            continue
        o  = row["open"].values[0]
        h  = row["high"].values[0]
        lw = row["low"].values[0]
        c  = row["close"].values[0]
        if any(pd.isna(v) for v in [o, h, lw, c]):
            continue

        # Map price → y-index (invert: high price = low y-index in imshow)
        def price_to_yi(p: float) -> float:
            p0 = price_rows_desc[0]  # highest price = y=0
            return (p0 - p) / ES_TICK

        yi_o  = price_to_yi(o)
        yi_c  = price_to_yi(c)
        yi_h  = price_to_yi(h)
        yi_lw = price_to_yi(lw)

        body_color = GREEN if c >= o else RED
        ax_top.plot(
            [j, j], [yi_h, yi_lw],
            color="#888888", linewidth=0.8, zorder=3, solid_capstyle="butt",
        )
        body_lo     = min(yi_o, yi_c)
        body_hi     = max(yi_o, yi_c)
        body_height = max(body_hi - body_lo, 0.15)
        ax_top.add_patch(mpatches.Rectangle(
            (j - 0.32, body_lo), 0.64, body_height,
            facecolor=body_color, edgecolor="none", alpha=0.9, zorder=4,
        ))

    # --- Adam's level (horizontal gold dashed) ----------------------------------
    if level_i is not None:
        ax_top.axhline(level_i, color=GOLD, linewidth=2.0, linestyle="--", alpha=0.95, zorder=5)
        ax_top.text(
            n_bars - 0.3, level_i - 0.5,
            f"Level {level:.2f}",
            color=GOLD, fontsize=10, fontweight="bold", ha="right", va="bottom",
            zorder=6,
            bbox=dict(facecolor=BG, edgecolor=GOLD, alpha=0.8, pad=2),
        )

    # --- Three vertical lines ---------------------------------------------------
    def _draw_vline(ax, j: int | None, color: str, style: str, label: str,
                    ts_et: pd.Timestamp | None, y_text: float = 1.5) -> None:
        if j is None:
            return
        ax.axvline(j, color=color, linewidth=1.8, linestyle=style, alpha=0.9, zorder=5)
        label_str = _fmt_et(ts_et) if ts_et is not None else label
        ax.text(
            j + 0.2, y_text, label_str,
            color=color, fontsize=8, fontweight="bold", va="top", zorder=6,
            bbox=dict(facecolor=BG, edgecolor=color, alpha=0.7, pad=1),
        )

    _draw_vline(ax_top, post_j,  WHITE, ":",  "Call",  post_et,  y_text=1.5)
    _draw_vline(ax_top, entry_j, GOLD,  "--", "Entry", entry_et, y_text=4.5)
    _draw_vline(ax_top, exit_j,  CYAN,  "--", "Exit",  exit_et,  y_text=7.5)

    # --- Direction arrow annotation ----------------------------------------------
    dir_sym  = "^ LONG" if call["direction"] == "long" else "v SHORT"
    ann_yi   = (level_i if level_i is not None else n_price // 2)
    offset_j = max(3, int(n_bars * 0.08))
    anchor_j = post_j if post_j is not None else 0
    ax_top.annotate(
        dir_sym,
        xy=(anchor_j, ann_yi),
        xytext=(min(anchor_j + offset_j, n_bars - 2), max(ann_yi - 4, 2)),
        color=GOLD,
        fontsize=11,
        fontweight="bold",
        arrowprops=dict(arrowstyle="-|>", color=GOLD, lw=1.8),
        bbox=dict(facecolor=BG, edgecolor=GOLD, alpha=0.85, pad=3),
        zorder=7,
    )

    # --- Y-axis: price labels ----------------------------------------------------
    step_y = max(1, n_price // 16)
    ytick_positions = list(range(0, n_price, step_y))
    ytick_labels    = [f"{price_rows_desc[i]:.2f}" for i in ytick_positions]
    ax_top.set_yticks(ytick_positions)
    ax_top.set_yticklabels(ytick_labels, fontsize=9, color=LIGHT)
    ax_top.set_ylabel("/ES Price", color=LIGHT, fontsize=11)
    ax_top.tick_params(axis="x", labelbottom=False)
    ax_top.set_xlim(-0.5, n_bars - 0.5)
    ax_top.set_ylim(n_price - 0.5, -0.5)

    # ═══════════════════════════════════════════════════════════════════════════
    # BOTTOM PANEL — Delta bars + cumulative delta
    # ═══════════════════════════════════════════════════════════════════════════

    bar_delta_vals  = bar_delta.values
    bar_colors_list = [GREEN if d >= 0 else RED for d in bar_delta_vals]
    ax_bot.bar(range(n_bars), bar_delta_vals, color=bar_colors_list, width=0.85, alpha=0.8)

    # Cumulative delta line (right y-axis)
    ax_cum = ax_bot.twinx()
    ax_cum.set_facecolor(BG)
    ax_cum.step(
        range(n_bars), cum_delta.values,
        color=WHITE, linewidth=1.8, where="post", alpha=0.85,
    )
    ax_cum.axhline(0, color=GREY, linewidth=0.6)
    ax_cum.set_ylabel("Cum Delta", color=LIGHT, fontsize=9)
    ax_cum.tick_params(axis="y", labelcolor=LIGHT, labelsize=8)

    ax_bot.axhline(0, color=GREY, linewidth=0.5)
    for j, color in [(post_j, WHITE), (entry_j, GOLD), (exit_j, CYAN)]:
        if j is not None:
            ax_bot.axvline(j, color=color, linewidth=1.8,
                           linestyle=":" if j == post_j else "--", alpha=0.85)
    ax_bot.set_ylabel("Net Delta / bar", color=LIGHT, fontsize=9)
    ax_bot.tick_params(axis="y", labelcolor=LIGHT, labelsize=8)

    # X-axis: time labels in ET
    step_x = max(1, n_bars // 15)
    xtick_positions = list(range(0, n_bars, step_x))
    xtick_labels    = [
        bar_times[j].tz_convert(TZ_ET).strftime("%H:%M ET")
        for j in xtick_positions
    ]
    ax_bot.set_xticks(xtick_positions)
    ax_bot.set_xticklabels(xtick_labels, rotation=45, ha="right", fontsize=8, color=LIGHT)
    ax_bot.tick_params(axis="x", colors=LIGHT)
    ax_bot.set_xlim(-0.5, n_bars - 0.5)

    # ═══════════════════════════════════════════════════════════════════════════
    # TITLE + ABSORPTION READ  (no overlap — two separate fig.text calls)
    # ═══════════════════════════════════════════════════════════════════════════

    pnl_str   = f"+{call['points_net']:.2f}" if call["points_net"] > 0 else f"{call['points_net']:.2f}"
    pnl_color = GREEN if call["points_net"] > 0 else RED

    # Date line: use ET timezone
    post_date_str  = post_et.strftime("%Y-%m-%d") if post_et else ""
    post_time_str  = _fmt_et(post_et)
    entry_time_str = _fmt_et(entry_et)
    exit_time_str  = _fmt_et(exit_et)

    title1 = (
        f"[{call['label']}]  {call['direction'].upper()} @ {level}  |  "
        f"{post_date_str}  call {post_time_str}  entry {entry_time_str}  exit {exit_time_str}  |  "
        f"P&L: {pnl_str} pts  ({call['exit_reason']})"
    )
    title2 = f"Order-flow read: {absorption_read}"

    # Two separate text objects with generous vertical separation
    fig.text(
        0.5, 0.975, title1,
        ha="center", va="top",
        color=pnl_color, fontsize=10, fontweight="bold",
        transform=fig.transFigure,
    )
    fig.text(
        0.5, 0.952, title2,
        ha="center", va="top",
        color="#cccccc", fontsize=9, style="italic",
        transform=fig.transFigure,
    )

    # ── Legend strip at top of heatmap panel ─────────────────────────────────
    legend_items = [
        mpatches.Patch(color="#F5A623", label="High resting liq"),
        mpatches.Patch(color=GREEN,     label="Bid-heavy (demand)"),
        mpatches.Patch(color=RED,       label="Ask-heavy (supply)"),
        mpatches.Patch(color=GOLD,      label="Level / Entry"),
        mpatches.Patch(color=WHITE,     label="Call time"),
        mpatches.Patch(color=CYAN,      label="Exit"),
    ]
    ax_top.legend(
        handles=legend_items,
        loc="upper left",
        fontsize=8,
        facecolor=BG,
        edgecolor="#444444",
        labelcolor=LIGHT,
        ncol=6,
        framealpha=0.85,
    )

    # ── Save ─────────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(
        out_path,
        dpi=DPI,
        bbox_inches="tight",
        facecolor=BG,
        edgecolor="none",
    )
    plt.close(fig)
    print(f"    saved -> {out_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  BOOK VERIFICATION  (printed to stdout, not rendered)
# ═══════════════════════════════════════════════════════════════════════════════

def verify_book_reconstruction(
    df_raw: pd.DataFrame,
    book_df: pd.DataFrame,
    level: float,
    check_ts: pd.Timestamp,
) -> None:
    """Print a manual vs reconstructed book check at a specific level + time.

    Manual method: stream raw A/C/M events for side='B' at `level` up to
    `check_ts`, track net resting size, compare to book_df.

    Also prints bid/ask orientation (bid should be below, ask above mid price).
    """
    bar_ts = check_ts.floor(f"{BAR_MINUTES}min")
    print(f"\n  [BOOK VERIFICATION] at price={level:.2f}, bar={bar_ts} UTC")

    # ── Manual count at level ──────────────────────────────────────────────────
    df_tight = df_raw[(df_raw["price"] >= 5000) & (df_raw["price"] <= 6500)].copy()
    acm = df_tight[df_tight["action"].isin(["A", "C", "M"])].sort_values("ts")

    book_manual: dict[int, tuple[float, str, int]] = {}
    for row in acm[acm["ts"] < check_ts].itertuples():
        if row.action == "A":
            book_manual[row.order_id] = (
                round(row.price / ES_TICK) * ES_TICK, row.side, int(row.size)
            )
        elif row.action == "C":
            book_manual.pop(row.order_id, None)
        elif row.action == "M":
            book_manual[row.order_id] = (
                round(row.price / ES_TICK) * ES_TICK, row.side, int(row.size)
            )

    manual_bid = sum(sz for (px, sd, sz) in book_manual.values()
                     if sd == "B" and abs(px - level) < 0.001)
    manual_ask = sum(sz for (px, sd, sz) in book_manual.values()
                     if sd == "A" and abs(px - level) < 0.001)

    # Reconstructed value from book_df at that bar
    row_book = book_df[(book_df["bar"] == bar_ts) & (book_df["price_tick"].sub(level).abs() < 0.001)]
    recon_bid = int(row_book["bid_size"].values[0]) if not row_book.empty else 0
    recon_ask = int(row_book["ask_size"].values[0]) if not row_book.empty else 0

    print(f"    Manual stream bid_size={manual_bid}, ask_size={manual_ask}")
    print(f"    Reconstructed  bid_size={recon_bid}, ask_size={recon_ask}")
    bid_ok = abs(manual_bid - recon_bid) <= 10  # allow small rounding/order splits
    ask_ok = abs(manual_ask - recon_ask) <= 10
    print(f"    Bid match: {'OK' if bid_ok else 'DISCREPANCY'} (diff={recon_bid-manual_bid})")
    print(f"    Ask match: {'OK' if ask_ok else 'DISCREPANCY'} (diff={recon_ask-manual_ask})")
    print(f"    Note: small diff (<10) = orders Modified from other price levels into {level:.2f}")

    # ── Bid/Ask orientation near current price ─────────────────────────────────
    bids_agg: dict[float, int] = {}
    asks_agg: dict[float, int] = {}
    for (px, sd, sz) in book_manual.values():
        if 4900 <= px <= 6200:   # sane ES range
            if sd == "B":
                bids_agg[px] = bids_agg.get(px, 0) + sz
            else:
                asks_agg[px] = asks_agg.get(px, 0) + sz

    # Find recent trades near check_ts to establish mid
    t_start = check_ts - pd.Timedelta(minutes=2)
    trades_near = df_raw[
        (df_raw["action"] == "T") & (df_raw["ts"] >= t_start) & (df_raw["ts"] < check_ts)
    ]
    mid_est = float(trades_near["price"].mean()) if not trades_near.empty else level
    print(f"\n    Mid price estimate (recent trades before check_ts): {mid_est:.2f}")

    # Find the DOMINANT bid/ask range near mid (within 20 pts)
    bids_near = {px: sz for px, sz in bids_agg.items() if abs(px - mid_est) <= 20}
    asks_near = {px: sz for px, sz in asks_agg.items() if abs(px - mid_est) <= 20}

    if bids_near:
        bid_wt = sum(px * sz for px, sz in bids_near.items()) / sum(bids_near.values())
        print(f"    Resting bids (B) within 20 pts of mid: "
              f"{len(bids_near)} levels, weighted avg px={bid_wt:.2f}")
    if asks_near:
        ask_wt = sum(px * sz for px, sz in asks_near.items()) / sum(asks_near.values())
        print(f"    Resting asks (A) within 20 pts of mid: "
              f"{len(asks_near)} levels, weighted avg px={ask_wt:.2f}")

    if bids_near and asks_near:
        if bid_wt < mid_est and ask_wt > mid_est:
            print(f"    ORIENTATION: CORRECT -- bids below mid ({bid_wt:.2f} < {mid_est:.2f}), "
                  f"asks above ({ask_wt:.2f} > {mid_est:.2f})")
        elif bid_wt > mid_est:
            print(f"    ORIENTATION WARNING: dominant bid weight {bid_wt:.2f} ABOVE mid {mid_est:.2f}")
        else:
            print(f"    ORIENTATION WARNING: dominant ask weight {ask_wt:.2f} BELOW mid {mid_est:.2f}")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# 10.  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    # UTF-8 stdout (critical on Windows cp1252)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 72)
    print("MBO ENGINE  --  Resting-Book Heatmap + Footprint  (/ES calls)")
    print("  Window: post_ts-15min -> exit_ts+5min  |  All times in ET")
    print("=" * 72)

    # ── Step 1: Select calls ───────────────────────────────────────────────────
    calls = select_calls(CSV_PATH, MBO_DIR)
    print("\nSelected calls:")
    for c in calls:
        sign = "+" if c["points_net"] > 0 else ""
        post_et  = _et(c["post_ts"])
        entry_et = _et(c["entry_ts"])
        exit_et  = _et(c["exit_ts"])
        print(
            f"  [{c['label']:6s}]  post_id={c['post_id']}  "
            f"{(post_et.strftime('%Y-%m-%d') + ' ' if post_et else '')}{_fmt_et(post_et)}  "
            f"{c['direction']:5s} @ {c['level']}  "
            f"entry={_fmt_et(entry_et)}  exit={_fmt_et(exit_et)}  "
            f"pts={sign}{c['points_net']}  ({c['exit_reason']})"
        )

    # ── Step 2-9: Process each call ────────────────────────────────────────────
    png_paths: list[str] = []
    report_rows: list[dict] = []

    for call in calls:
        print(f"\n{'─'*60}")
        print(f"Processing [{call['label']}]: {call['post_id']}")

        # Load data
        df = load_mbo(call["post_id"])
        print(
            f"  Loaded {len(df):,} events  "
            f"(T={int((df['action']=='T').sum()):,}  "
            f"A={int((df['action']=='A').sum()):,}  "
            f"C={int((df['action']=='C').sum()):,}  "
            f"M={int((df['action']=='M').sum()):,}  "
            f"F={int((df['action']=='F').sum()):,})"
        )

        # Verify price sanity
        t_events = df[df["action"] == "T"]
        if not t_events.empty:
            print(
                f"  Trade price range: "
                f"{t_events['price'].min():.2f} - {t_events['price'].max():.2f}  "
                f"(expected /ES ~5000-6000)"
            )

        # Data time bounds (for clamping)
        data_start = df["ts"].min()
        data_end   = df["ts"].max()
        print(f"  MBO data range: {data_start} -> {data_end}")

        # Zoom grid (trade-spanning window)
        bar_times, price_levels = make_zoom_grid(
            post_ts    = call["post_ts"],
            entry_ts   = call["entry_ts"],
            exit_ts    = call["exit_ts"],
            level      = call["level"],
            exit_px    = call["exit_px"],
            data_start = data_start,
            data_end   = data_end,
        )
        win_start_et = _fmt_et(_et(bar_times[0]))
        win_end_et   = _fmt_et(_et(bar_times[-1]))
        print(
            f"  Window: {win_start_et} - {win_end_et}  "
            f"({len(bar_times)} bars)  "
            f"price: {price_levels[0]:.2f} - {price_levels[-1]:.2f}"
        )

        # Footprint
        fp = extract_trades(df, bar_times)
        fp_win    = fp[fp["bar"].isin(bar_times)]
        t_total   = int(fp_win["total"].sum())
        ask_vol   = int(fp_win["ask_vol"].sum())
        bid_vol   = int(fp_win["bid_vol"].sum())
        net_delta = ask_vol - bid_vol
        print(
            f"  Footprint window: {t_total:,} contracts  "
            f"buy={ask_vol:,}  sell={bid_vol:,}  net_delta={net_delta:+,}"
        )

        # Book reconstruction
        book_df = reconstruct_book(df, bar_times)
        if not book_df.empty:
            snap_rest      = book_df[book_df["bar"].isin(bar_times)]
            total_rest_bid = int(snap_rest["bid_size"].sum())
            total_rest_ask = int(snap_rest["ask_size"].sum())
            print(
                f"  Book (zoom window): total resting bid={total_rest_bid:,}  "
                f"ask={total_rest_ask:,}  "
                f"price levels tracked: {book_df['price_tick'].nunique()}"
            )

        # Book verification (winner only — avoids extra compute for losers)
        if call["label"] == "WINNER" and call["entry_ts"] is not None:
            check_ts = call["entry_ts"] + pd.Timedelta(minutes=4)
            verify_book_reconstruction(df, book_df, call["level"], check_ts)

        # Candles
        candles = build_candles(df, bar_times)

        # Verify candle direction for winner
        if call["label"] == "WINNER" and call["entry_ts"] is not None and call["exit_ts"] is not None:
            entry_bar = call["entry_ts"].floor(f"{BAR_MINUTES}min")
            exit_bar  = call["exit_ts"].floor(f"{BAR_MINUTES}min")
            c_entry = candles[candles["bar"] == entry_bar]
            c_exit  = candles[candles["bar"] == exit_bar]
            if not c_entry.empty and not c_exit.empty:
                open_at_entry  = c_entry["open"].values[0]
                close_at_exit  = c_exit["close"].values[0]
                price_move     = close_at_exit - open_at_entry if not pd.isna(close_at_exit) and not pd.isna(open_at_entry) else None
                print(f"  WINNER candle check: open@entry={open_at_entry:.2f}  close@exit={close_at_exit:.2f}  "
                      f"move={price_move:+.2f}" if price_move is not None else
                      f"  WINNER candle check: open@entry={open_at_entry}  close@exit={close_at_exit}")
                if price_move is not None and price_move > 15:
                    print(f"    -> Candles RISE entry->exit consistent with +{call['points_net']:.0f} pts target")
                elif price_move is not None:
                    print(f"    -> Candles show {price_move:+.2f} pts (expected ~+{call['points_net']:.0f})")

        # Call bar index (for absorption read, anchored on post_ts)
        call_bar = call["post_ts"].floor(f"{BAR_MINUTES}min")
        if call_bar in bar_times:
            call_j = bar_times.index(call_bar)
        else:
            diffs = [abs((b - call_bar).total_seconds()) for b in bar_times]
            call_j = int(np.argmin(diffs))

        # Absorption read
        absorption = compute_absorption_read(fp, book_df, call, bar_times, call_j)
        print(f"  Read: {absorption}")

        # Render
        out_path = OUT_DIR / f"mbo_{call['post_id']}.png"
        render_png(
            fp=fp,
            book_df=book_df,
            candles=candles,
            call=call,
            bar_times=bar_times,
            price_levels=price_levels,
            absorption_read=absorption,
            out_path=out_path,
        )
        png_paths.append(str(out_path))
        report_rows.append({
            "label":      call["label"],
            "post_id":    call["post_id"],
            "pts":        call["points_net"],
            "t_total":    t_total,
            "ask_vol":    ask_vol,
            "bid_vol":    bid_vol,
            "net_delta":  net_delta,
            "absorption": absorption,
            "png":        str(out_path),
        })

    # ── Final report ───────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("REPORT")
    print(f"{'='*72}")
    for r in report_rows:
        sign = "+" if r["pts"] > 0 else ""
        print(
            f"[{r['label']:6s}]  {sign}{r['pts']:.2f} pts  "
            f"contracts={r['t_total']:,}  "
            f"buy={r['ask_vol']:,}  sell={r['bid_vol']:,}  "
            f"net_delta={r['net_delta']:+,}"
        )
        print(f"         Read: {r['absorption']}")
        print(f"         PNG:  {r['png']}")
        print()

    print("Done. Check PNGs for heatmap + absorption signal.")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
