"""Order-flow viewer API — footprint + DOM + tape endpoints.

Source-agnostic response shapes: historical MBO parquets populate these now;
a future live WebSocket feed emits the IDENTICAL JSON shapes without changing
any of these route bodies.

All times are epoch-ms UTC over the wire; the client formats ET.
"""

from __future__ import annotations

import contextlib
import io
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

# ── Project root on sys.path so the engine import works when the module is
#    loaded directly (e.g. in tests that don't go through api.main) ───────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research._edgehunt.mbo_engine import (
    CSV_PATH,
    MBO_DIR,
    load_mbo,
    make_zoom_grid,
    reconstruct_book,
)

router = APIRouter(prefix="/api/orderflow", tags=["orderflow"])

# ── Constants ─────────────────────────────────────────────────────────────────
BOOK_DEPTH: int = 40    # max bid/ask levels per book snapshot (each side)
BOOK_CACHE_MAX: int = 2  # max post_ids with cached 1s book snapshots
TRADES_CACHE_MAX: int = 2  # max post_ids with cached full-window trades
DEFAULT_BAR: str = "30s"
DEFAULT_TICK: float = 0.25   # /ES minimum price increment

# ── In-process 1s book cache (one reconstruct per call) ───────────────────────
_BOOK_CACHE: OrderedDict[str, dict[str, list[dict]]] = OrderedDict()
# ── In-process trades cache (T events only; small vs full MBO) ────────────────
_TRADES_CACHE: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_bar_freq(bar: str) -> str:
    """Convert user bar string (e.g. '30s', '1m', '2m') to a pandas freq string.

    Raises HTTP 400 for unrecognised formats, non-integer components, or
    non-positive values (e.g. '0s', '-1m', 'foo').
    """
    s = bar.strip().lower()
    if s.endswith("s"):
        try:
            n = int(s[:-1])
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid bar format {bar!r} — expected e.g. '30s' or '1m'",
            )
        if n <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Bar seconds must be positive, got {bar!r}",
            )
        return f"{n}s"
    if s.endswith("m"):
        try:
            n = int(s[:-1])
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid bar format {bar!r} — expected e.g. '30s' or '1m'",
            )
        if n <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Bar minutes must be positive, got {bar!r}",
            )
        return f"{n}min"
    raise HTTPException(
        status_code=400,
        detail=f"Invalid bar format {bar!r} — expected e.g. '30s' or '1m'",
    )


def _validate_post_id(post_id: str) -> None:
    """Raise HTTP 400 if post_id is not a plain integer string.

    This keeps a clean error message for bad query params AND neutralises
    path-traversal attempts (the int() coercion is already there but only
    raised as an uncaught ValueError → 500).
    """
    try:
        int(post_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"post_id must be numeric, got {post_id!r}",
        )


def _ts_ms(ts: pd.Timestamp | None) -> int | None:
    """Epoch milliseconds (UTC) from a tz-aware Timestamp."""
    if ts is None:
        return None
    return int(ts.value // 1_000_000)


def _normalize_ts(val: Any) -> pd.Timestamp | None:
    """Parse any timestamp-like value to a UTC-aware Timestamp."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    ts = pd.Timestamp(val)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _load_all_calls() -> pd.DataFrame:
    """Load the calls CSV and annotate each row with has_mbo."""
    df = pd.read_csv(CSV_PATH)
    df["has_mbo"] = df["post_id"].apply(
        lambda pid: (MBO_DIR / f"{int(pid)}_mbo.parquet").exists()
    )
    return df


def _get_row(post_id: str) -> dict[str, Any]:
    """Return the CSV row for *post_id* as a dict; raise 404 if not found."""
    df = _load_all_calls()
    mask = df["post_id"].astype(str) == str(post_id)
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"post_id {post_id!r} not found in calls CSV")
    return df[mask].iloc[0].to_dict()


def _require_mbo(post_id: str) -> None:
    """Raise 404 if the MBO parquet file does not exist for *post_id*."""
    pq = MBO_DIR / f"{int(post_id)}_mbo.parquet"
    if not pq.exists():
        raise HTTPException(status_code=404, detail=f"No MBO parquet for post_id {post_id!r}")


def _window_bounds(row: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return (window_start, window_end) using make_zoom_grid's ±15/5-min padding.

    Reuses the engine's grid so all endpoints share the same window definition.
    """
    post_ts  = _normalize_ts(row.get("post_ts"))
    entry_ts = _normalize_ts(row.get("entry_ts"))
    exit_ts  = _normalize_ts(row.get("exit_ts"))
    level    = float(row["level"])
    raw_px   = row.get("exit_px")
    exit_px  = (
        float(raw_px)
        if raw_px is not None and not (isinstance(raw_px, float) and np.isnan(raw_px))
        else float("nan")
    )
    # make_zoom_grid returns 1-min bar_times; we only need the window edges
    bar_times_1m, _ = make_zoom_grid(
        post_ts=post_ts,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        level=level,
        exit_px=exit_px,
    )
    return bar_times_1m[0], bar_times_1m[-1]


def _bar_times_at(
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    freq: str,
) -> list[pd.Timestamp]:
    """Generate bar boundary timestamps at *freq* resolution spanning the window."""
    start = window_start.floor(freq)
    end   = window_end.ceil(freq)
    return list(pd.date_range(start, end, freq=freq))


def _build_footprint(
    df: pd.DataFrame,
    bar_times: list[pd.Timestamp],
    tick: float,
    freq: str,
) -> pd.DataFrame:
    """Build per-(bar, price_tick) bid/ask volumes from T (trade-print) events.

    Aggressor convention (matches Databento CME MBO):
      side='B'  buy aggressor, hit the ask  → ask_vol  (+delta)
      side='A'  sell aggressor, hit the bid → bid_vol  (−delta)
    """
    trades = df[df["action"] == "T"].copy()
    if trades.empty:
        return pd.DataFrame(columns=["bar", "price_tick", "bid_vol", "ask_vol", "delta"])

    trades["price_tick"] = (trades["price"] / tick).round() * tick
    trades["bar"] = trades["ts"].dt.floor(freq)

    ask_grp = (
        trades[trades["side"] == "B"]
        .groupby(["bar", "price_tick"])["size"]
        .sum()
        .rename("ask_vol")
    )
    bid_grp = (
        trades[trades["side"] == "A"]
        .groupby(["bar", "price_tick"])["size"]
        .sum()
        .rename("bid_vol")
    )
    fp = pd.concat([bid_grp, ask_grp], axis=1).fillna(0).astype(int).reset_index()
    fp["delta"] = fp["ask_vol"] - fp["bid_vol"]
    return fp


def _build_candles(
    df: pd.DataFrame,
    bar_times: list[pd.Timestamp],
    freq: str,
) -> pd.DataFrame:
    """Build OHLC candles from T events at the requested bar resolution."""
    trades = df[df["action"] == "T"].copy()
    bar_df = pd.DataFrame({"bar": pd.DatetimeIndex(bar_times)})

    if trades.empty:
        bar_df[["open", "high", "low", "close"]] = float("nan")
        return bar_df

    trades["bar"] = trades["ts"].dt.floor(freq)
    ohlc = (
        trades.groupby("bar")["price"]
        .agg(open="first", high="max", low="min", close="last")
        .reset_index()
    )
    ohlc = bar_df.merge(ohlc, on="bar", how="left")
    # Forward-fill to propagate close through gap (no-trade) bars
    ohlc["close"] = ohlc["close"].ffill()
    ohlc[["open", "high", "low"]] = ohlc[["open", "high", "low"]].ffill()
    return ohlc


@contextlib.contextmanager
def _silence():
    """Suppress stdout — mutes reconstruct_book's progress prints in API context."""
    old_out = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old_out


def _book_snapshots_from_df(
    book_df: pd.DataFrame,
    bar_times: list[pd.Timestamp],
) -> list[dict]:
    """Build compact book snapshots for *bar_times* from a reconstructed book_df."""
    snapshots: list[dict] = []
    for bt in bar_times:
        bar_book = book_df[book_df["bar"] == bt]

        bids_df = bar_book[bar_book["bid_size"] > 0].sort_values("price_tick", ascending=False)
        asks_df = bar_book[bar_book["ask_size"] > 0].sort_values("price_tick", ascending=True)

        # De-cross: CME MBO data can contain implied/synthetic orders that produce
        # a crossed book in the raw feed.  When the raw best-bid >= raw best-ask,
        # split at the midpoint so the displayed book is always un-crossed.
        if not bids_df.empty and not asks_df.empty:
            raw_best_bid = float(bids_df["price_tick"].iloc[0])
            raw_best_ask = float(asks_df["price_tick"].iloc[0])
            if raw_best_bid >= raw_best_ask:
                mid = (raw_best_bid + raw_best_ask) / 2.0
                bids_df = bids_df[bids_df["price_tick"] < mid]
                asks_df = asks_df[asks_df["price_tick"] > mid]

        bids_list = [
            {"price": round(float(r["price_tick"]), 2), "size": int(r["bid_size"])}
            for _, r in bids_df.head(BOOK_DEPTH).iterrows()
        ]
        asks_list = [
            {"price": round(float(r["price_tick"]), 2), "size": int(r["ask_size"])}
            for _, r in asks_df.head(BOOK_DEPTH).iterrows()
        ]

        snapshots.append({
            "t":    _ts_ms(bt),
            "bids": bids_list,
            "asks": asks_list,
        })
    return snapshots


def _get_cached_book_1s(post_id: str) -> list[dict]:
    """Return the full-window 1s book snapshots for *post_id*, building cache on miss."""
    if post_id in _BOOK_CACHE:
        _BOOK_CACHE.move_to_end(post_id)
        return _BOOK_CACHE[post_id]["snapshots"]

    _validate_post_id(post_id)
    _require_mbo(post_id)
    row = _get_row(post_id)
    window_start, window_end = _window_bounds(row)
    bar_times = _bar_times_at(window_start, window_end, "1s")

    df = load_mbo(post_id)
    with _silence():
        book_df = reconstruct_book(df, bar_times)

    snapshots = _book_snapshots_from_df(book_df, bar_times)

    _BOOK_CACHE[post_id] = {"snapshots": snapshots}
    _BOOK_CACHE.move_to_end(post_id)
    while len(_BOOK_CACHE) > BOOK_CACHE_MAX:
        _BOOK_CACHE.popitem(last=False)

    return snapshots


def _get_cached_trades(post_id: str) -> list[dict[str, Any]]:
    """Return the full-window trade-print events for *post_id*, building cache on miss."""
    if post_id in _TRADES_CACHE:
        _TRADES_CACHE.move_to_end(post_id)
        return _TRADES_CACHE[post_id]

    _validate_post_id(post_id)
    _require_mbo(post_id)
    row = _get_row(post_id)
    window_start, window_end = _window_bounds(row)

    df = load_mbo(post_id)
    trades_df = (
        df[(df["action"] == "T") & (df["ts"] >= window_start) & (df["ts"] <= window_end)]
        .sort_values("ts")
    )

    trades = [
        {
            "t":     _ts_ms(r["ts"]),
            "price": round(float(r["price"]), 2),
            "size":  int(r["size"]),
            "side":  str(r["side"]),
        }
        for _, r in trades_df.iterrows()
    ]

    _TRADES_CACHE[post_id] = trades
    _TRADES_CACHE.move_to_end(post_id)
    while len(_TRADES_CACHE) > TRADES_CACHE_MAX:
        _TRADES_CACHE.popitem(last=False)

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/calls")
def api_orderflow_calls() -> dict[str, Any]:
    """Selector list: all calls with MBO availability flag."""
    df = _load_all_calls()
    calls = []
    for _, r in df.iterrows():
        pnl = r.get("points_net")
        calls.append({
            "post_id":     str(int(r["post_id"])),
            "date":        str(r["date"]),
            "direction":   str(r["direction"]),
            "level":       float(r["level"]),
            "points_net":  float(pnl) if pnl is not None and not (isinstance(pnl, float) and np.isnan(pnl)) else None,
            "exit_reason": str(r.get("exit_reason", "")),
            "has_mbo":     bool(r["has_mbo"]),
        })
    return {"calls": calls}


@router.get("/{post_id}/meta")
def api_orderflow_meta(post_id: str) -> dict[str, Any]:
    """Call metadata + window bounds (all times epoch-ms UTC)."""
    _validate_post_id(post_id)
    row = _get_row(post_id)
    post_ts  = _normalize_ts(row.get("post_ts"))
    entry_ts = _normalize_ts(row.get("entry_ts"))
    exit_ts  = _normalize_ts(row.get("exit_ts"))
    raw_px   = row.get("exit_px")
    exit_px  = (
        float(raw_px)
        if raw_px is not None and not (isinstance(raw_px, float) and np.isnan(raw_px))
        else None
    )
    pnl = row.get("points_net")

    window_start, window_end = _window_bounds(row)

    # Read symbol from parquet if available; fall back to generic label
    symbol = "ES.c.0"
    pq = MBO_DIR / f"{int(post_id)}_mbo.parquet"
    if pq.exists():
        try:
            sym_df = pd.read_parquet(pq, columns=["symbol"]).head(1)
            if not sym_df.empty:
                symbol = str(sym_df["symbol"].iloc[0])
        except Exception:
            pass

    return {
        "post_id":      str(int(row["post_id"])),
        "symbol":       symbol,
        "post_ts":      _ts_ms(post_ts),
        "entry_ts":     _ts_ms(entry_ts),
        "exit_ts":      _ts_ms(exit_ts),
        "exit_px":      exit_px,
        "level":        float(row["level"]),
        "direction":    str(row["direction"]),
        "points_net":   float(pnl) if pnl is not None and not (isinstance(pnl, float) and np.isnan(pnl)) else None,
        "exit_reason":  str(row.get("exit_reason", "")),
        "window_start": _ts_ms(window_start),
        "window_end":   _ts_ms(window_end),
        "tz":           "America/New_York",
    }


@router.get("/{post_id}/candles")
def api_orderflow_candles(
    post_id: str,
    bar: str = Query(default=DEFAULT_BAR, description="Bar resolution, e.g. '30s', '1m'"),
) -> dict[str, Any]:
    """OHLC candles at the requested bar resolution (default 30s).

    Shape: { bars: [{ t, o, h, l, c }] }  — t is epoch-ms UTC.
    """
    _validate_post_id(post_id)
    _require_mbo(post_id)
    row = _get_row(post_id)
    freq = _parse_bar_freq(bar)
    window_start, window_end = _window_bounds(row)
    bar_times = _bar_times_at(window_start, window_end, freq)

    df = load_mbo(post_id)
    # Candles only need events inside the window
    df_win = df[(df["ts"] >= window_start) & (df["ts"] <= window_end)]

    candles = _build_candles(df_win, bar_times, freq)

    bars_out = []
    for _, r in candles.iterrows():
        if pd.isna(r["close"]):
            continue  # skip leading no-data bars
        bars_out.append({
            "t": _ts_ms(r["bar"]),
            "o": round(float(r["open"]),  2) if not pd.isna(r["open"])  else None,
            "h": round(float(r["high"]),  2) if not pd.isna(r["high"])  else None,
            "l": round(float(r["low"]),   2) if not pd.isna(r["low"])   else None,
            "c": round(float(r["close"]), 2),
        })
    return {"bars": bars_out}


@router.get("/{post_id}/footprint")
def api_orderflow_footprint(
    post_id: str,
    bar: str = Query(default=DEFAULT_BAR, description="Bar resolution, e.g. '30s', '1m'"),
    tick: float = Query(default=DEFAULT_TICK, description="/ES tick size"),
) -> dict[str, Any]:
    """Per-bar, per-price-tick footprint with bid/ask volume and delta.

    Shape: { bars: [{ t, cells: [{price, bidVol, askVol}], delta, cumDelta }] }
    """
    _validate_post_id(post_id)
    if tick <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"tick must be positive, got {tick} (divides by zero)",
        )
    _require_mbo(post_id)
    row = _get_row(post_id)
    freq = _parse_bar_freq(bar)
    window_start, window_end = _window_bounds(row)
    bar_times = _bar_times_at(window_start, window_end, freq)

    df = load_mbo(post_id)
    df_win = df[(df["ts"] >= window_start) & (df["ts"] <= window_end)]

    fp = _build_footprint(df_win, bar_times, tick, freq)

    # Per-bar delta (aggregated across all price levels)
    bar_delta_s = fp.groupby("bar")["delta"].sum()

    cum = 0
    bars_out = []
    for bt in bar_times:
        bar_fp = fp[fp["bar"] == bt]
        cells = []
        for _, r in bar_fp.iterrows():
            if r["bid_vol"] == 0 and r["ask_vol"] == 0:
                continue
            cells.append({
                "price":  round(float(r["price_tick"]), 2),
                "bidVol": int(r["bid_vol"]),
                "askVol": int(r["ask_vol"]),
            })
        cells.sort(key=lambda c: c["price"])

        bar_d = int(bar_delta_s.get(bt, 0))
        cum += bar_d

        bars_out.append({
            "t":        _ts_ms(bt),
            "cells":    cells,
            "delta":    bar_d,
            "cumDelta": cum,
        })

    return {"bars": bars_out}


@router.get("/{post_id}/book")
def api_orderflow_book(
    post_id: str,
    bar: str = Query(default=DEFAULT_BAR, description="Bar resolution (legacy), e.g. '30s', '1m'"),
    res: str | None = Query(
        default=None,
        description="Snapshot resolution, e.g. '1s', '5s', '30s'; overrides bar when set",
    ),
    from_ms: int | None = Query(
        default=None,
        alias="from",
        description="Window start epoch-ms UTC — filters which snapshots are returned",
    ),
    to_ms: int | None = Query(
        default=None,
        alias="to",
        description="Window end epoch-ms UTC — filters which snapshots are returned",
    ),
) -> dict[str, Any]:
    """Resting book snapshots at each bar boundary for DOM ladder + heatmap.

    Shape: { snapshots: [{ t, bids: [{price,size}], asks: [{price,size}] }] }
    Depth is capped at BOOK_DEPTH levels per side.  Bids are sorted descending
    (best bid first); asks ascending (best ask first).  This means all bid
    prices < all ask prices in any non-degenerate snapshot.

    Use ``res`` (e.g. ``res=1s``) for sub-30s snapshot granularity so the DOM
    ladder can update smoothly while scrubbing.  Combine with ``from``/``to``
    (epoch-ms) to request only the visible time range and keep payload size
    reasonable (1 s over a 2-hour window = ~7 200 snapshots; 1 s over 5 min
    = 300 snapshots, which is a comfortable payload).

    ``res`` overrides ``bar`` when both are supplied; ``bar`` is retained for
    backward compatibility.
    """
    _validate_post_id(post_id)
    _require_mbo(post_id)
    row = _get_row(post_id)
    freq = _parse_bar_freq(res if res is not None else bar)
    window_start, window_end = _window_bounds(row)

    cached_1s = _get_cached_book_1s(post_id)

    t_from = (
        pd.Timestamp(from_ms, unit="ms", tz="UTC") if from_ms is not None else window_start
    )
    t_to = (
        pd.Timestamp(to_ms, unit="ms", tz="UTC") if to_ms is not None else window_end
    )

    # Snapshots at the requested resolution: serve from the 1s cache, filtering
    # to [from, to] and downsampling to coarser bar boundaries when needed.
    bar_times_full = _bar_times_at(window_start, window_end, freq)
    bar_times = [bt for bt in bar_times_full if t_from <= bt <= t_to]
    allowed_ts = {_ts_ms(bt) for bt in bar_times}

    snapshots = [s for s in cached_1s if s["t"] in allowed_ts]

    return {"snapshots": snapshots}


@router.get("/{post_id}/tape")
def api_orderflow_tape(
    post_id: str,
    from_ms: int | None = Query(default=None, alias="from", description="Start time epoch-ms UTC"),
    to_ms:   int | None = Query(default=None, alias="to",   description="End time epoch-ms UTC"),
    limit: int = Query(default=500, description="Max events returned"),
) -> dict[str, Any]:
    """Raw order tape — all MBO events in chronological order.

    Shape: { events: [{ t, action, side, price, size, order_id }] }
    Defaults to the call window; use from/to (epoch-ms UTC) to zoom.
    """
    _validate_post_id(post_id)
    _require_mbo(post_id)
    row = _get_row(post_id)
    window_start, window_end = _window_bounds(row)

    t_start = (
        pd.Timestamp(from_ms, unit="ms", tz="UTC") if from_ms is not None else window_start
    )
    t_end = (
        pd.Timestamp(to_ms, unit="ms", tz="UTC") if to_ms is not None else window_end
    )

    df = load_mbo(post_id)
    tape = (
        df[(df["ts"] >= t_start) & (df["ts"] <= t_end)]
        .sort_values("ts")
        .head(limit)
    )

    events = [
        {
            "t":        _ts_ms(r["ts"]),
            "action":   str(r["action"]),
            "side":     str(r["side"]),
            "price":    round(float(r["price"]), 2),
            "size":     int(r["size"]),
            "order_id": int(r["order_id"]),
        }
        for _, r in tape.iterrows()
    ]
    return {"events": events}


@router.get("/{post_id}/trades")
def api_orderflow_trades(
    post_id: str,
    from_ms: int | None = Query(default=None, alias="from", description="Start time epoch-ms UTC"),
    to_ms: int | None = Query(default=None, alias="to", description="End time epoch-ms UTC"),
) -> dict[str, Any]:
    """Trade-print events only (action T) for tick-by-tick footprint/candle replay.

    Shape: { trades: [{ t, price, size, side }] }
    side follows MBO aggressor convention: B = buy aggressor (hit ask), A = sell aggressor (hit bid).
    Defaults to the call window; use from/to (epoch-ms UTC) to filter.
    """
    _validate_post_id(post_id)
    _require_mbo(post_id)
    row = _get_row(post_id)
    window_start, window_end = _window_bounds(row)

    t_start = (
        pd.Timestamp(from_ms, unit="ms", tz="UTC") if from_ms is not None else window_start
    )
    t_end = (
        pd.Timestamp(to_ms, unit="ms", tz="UTC") if to_ms is not None else window_end
    )
    start_ms = _ts_ms(t_start)
    end_ms = _ts_ms(t_end)

    cached = _get_cached_trades(post_id)
    trades = [t for t in cached if start_ms <= t["t"] <= end_ms]

    return {"trades": trades}
