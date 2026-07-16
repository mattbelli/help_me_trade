#!/usr/bin/env python3
"""Footprint / order-flow viewer for /ES — validate Adam's @adaamset calls.

Pulls TBBO data from Databento for 3 calls (max-winner, max-loser, closest-to-0
dud), builds footprint charts (bid_vol x ask_vol per 0.25-tick price row per
1-min time bar), and saves PNGs to data/research/_edgehunt/.

Budget: each window estimated (FREE) before pull; aborts if > $1.50/window.
Total for 3 windows should be ~$0.40-0.60.

Run (from repo root):
    PYTHONIOENCODING=utf-8 python -m research._edgehunt.footprint_viewer
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Project root on path ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
from matplotlib.cm import RdYlGn
from matplotlib.colors import TwoSlopeNorm

from research.databento_loader import pull_tbbo

# ── Constants ─────────────────────────────────────────────────────────────────
CSV_PATH = ROOT / "data" / "research" / "adam_trigger_study.csv"
OUT_DIR = ROOT / "data" / "research" / "_edgehunt"
ES_TICK = 0.25          # /ES minimum price increment
BAR_MINUTES = 1         # footprint time resolution
WINDOW_HOURS = 1        # ±1h around the call → 2h total pull
DISPLAY_PRICE_RANGE = 10.0   # ±10 pts around Adam's level in the chart
TEXT_MIN_VOL = 3        # only draw "bid×ask" text if total_vol >= this
BUDGET_PER_WINDOW = 1.50    # USD — budget guard, never waived


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: Pick 3 calls
# ─────────────────────────────────────────────────────────────────────────────

def select_calls(csv_path: Path) -> list[dict]:
    """Return [winner, loser, dud] from filled calls with points_net."""
    df = pd.read_csv(csv_path)
    filled = df[df["filled"].astype(str).str.strip().str.lower() == "true"].copy()
    filled["points_net"] = pd.to_numeric(filled["points_net"], errors="coerce")
    filled = filled.dropna(subset=["points_net"])

    winner_idx = filled["points_net"].idxmax()
    loser_idx = filled["points_net"].idxmin()
    winner_pnl = filled.loc[winner_idx, "points_net"]
    loser_pnl = filled.loc[loser_idx, "points_net"]

    # Dud: closest to 0 that is NOT at the same value as the winner or loser.
    # This avoids picking another full-stop (-11) or full-target (+19) when a
    # partial-exit row (e.g. +12.25 "time") exists — that's the interesting case.
    rest = filled[~filled.index.isin({winner_idx, loser_idx})]
    mid_candidates = rest[
        (rest["points_net"] != winner_pnl) & (rest["points_net"] != loser_pnl)
    ]
    if mid_candidates.empty:
        mid_candidates = rest  # fallback: nothing in between
    dud_idx = (mid_candidates["points_net"] - 0).abs().idxmin()

    calls = []
    for label, idx in [("WINNER", winner_idx), ("LOSER", loser_idx), ("DUD", dud_idx)]:
        row = filled.loc[idx]
        calls.append(
            {
                "label": label,
                "post_id": str(int(row["post_id"])),
                "post_ts": row["post_ts"],
                "direction": row["direction"],
                "level": float(row["level"]),
                "points_net": float(row["points_net"]),
                "exit_reason": str(row.get("exit_reason", "")),
            }
        )
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2+3: Pull TBBO window for a call
# ─────────────────────────────────────────────────────────────────────────────

def pull_window(call: dict) -> tuple[pd.DataFrame, float, str, str]:
    """Compute ±1h window, estimate cost, pull TBBO. Returns (df, cost, start, end)."""
    post_ts = pd.Timestamp(call["post_ts"]).tz_convert("UTC")
    start_ts = post_ts - timedelta(hours=WINDOW_HOURS)
    end_ts = post_ts + timedelta(hours=WINDOW_HOURS)

    # Format as ISO strings for the API
    start = start_ts.strftime("%Y-%m-%dT%H:%M:%S")
    end = end_ts.strftime("%Y-%m-%dT%H:%M:%S")

    # pull_tbbo does the estimate + guard + pull
    df, cost = pull_tbbo(start=start, end=end, budget_usd=BUDGET_PER_WINDOW)
    print(f"    pulled {len(df):,} records  (${cost:.4f})")
    return df, cost, start, end


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: Build footprint
# ─────────────────────────────────────────────────────────────────────────────

def build_footprint(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate TBBO trades into footprint cells (bar x price) → bid_vol, ask_vol.

    Footprint logic:
      side='A'  → buyer aggressor (hit ask) → ask_vol
      side='B'  → seller aggressor (hit bid) → bid_vol
    """
    if df.empty:
        return pd.DataFrame()

    # Filter to trade records only (action == 'T')
    trades = df[df["action"] == "T"].copy() if "action" in df.columns else df.copy()

    if trades.empty:
        return pd.DataFrame()

    # Ensure ts is datetime UTC
    if not pd.api.types.is_datetime64_any_dtype(trades["ts"]):
        trades["ts"] = pd.to_datetime(trades["ts"], utc=True)
    elif trades["ts"].dt.tz is None:
        trades["ts"] = trades["ts"].dt.tz_localize("UTC")

    # Round price to nearest /ES tick (0.25)
    trades["price_tick"] = (trades["price"] / ES_TICK).round() * ES_TICK

    # 1-min time bar
    trades["bar"] = trades["ts"].dt.floor(f"{BAR_MINUTES}min")

    ask_vol = (
        trades[trades["side"] == "A"]
        .groupby(["bar", "price_tick"])["size"]
        .sum()
        .rename("ask_vol")
    )
    bid_vol = (
        trades[trades["side"] == "B"]
        .groupby(["bar", "price_tick"])["size"]
        .sum()
        .rename("bid_vol")
    )

    fp = (
        pd.concat([bid_vol, ask_vol], axis=1)
        .fillna(0)
        .astype(int)
        .reset_index()
    )
    fp["delta"] = fp["ask_vol"] - fp["bid_vol"]
    fp["total"] = fp["ask_vol"] + fp["bid_vol"]
    return fp


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 continued: Render footprint PNG
# ─────────────────────────────────────────────────────────────────────────────

def render_footprint(fp: pd.DataFrame, call: dict, out_path: Path) -> None:
    """Render footprint heatmap + delta bar panel, save as PNG."""
    if fp.empty:
        print(f"    WARNING: no footprint data for {call['label']}")
        return

    post_ts = pd.Timestamp(call["post_ts"]).tz_convert("UTC")
    level = call["level"]

    # ── Zoom to price range ───────────────────────────────────────────────────
    lo = level - DISPLAY_PRICE_RANGE
    hi = level + DISPLAY_PRICE_RANGE
    fp_zoom = fp[(fp["price_tick"] >= lo) & (fp["price_tick"] <= hi)].copy()
    if fp_zoom.empty:
        # Fallback: use the full traded range
        lo, hi = fp["price_tick"].min(), fp["price_tick"].max()
        fp_zoom = fp.copy()

    # ── Pivot tables ──────────────────────────────────────────────────────────
    # Sort bars ascending (left→right = time), prices descending (top = high)
    bid_piv = fp_zoom.pivot_table(
        index="price_tick", columns="bar", values="bid_vol", fill_value=0
    ).sort_index(ascending=False)
    ask_piv = fp_zoom.pivot_table(
        index="price_tick", columns="bar", values="ask_vol", fill_value=0
    ).sort_index(ascending=False)
    delta_piv = fp_zoom.pivot_table(
        index="price_tick", columns="bar", values="delta", fill_value=0
    ).sort_index(ascending=False)

    bar_times = list(bid_piv.columns)   # pd.Timestamp list
    price_rows = list(bid_piv.index)    # float list, descending
    n_bars = len(bar_times)
    n_prices = len(price_rows)

    if n_bars == 0 or n_prices == 0:
        print(f"    WARNING: empty pivot for {call['label']}")
        return

    # ── Figure geometry ───────────────────────────────────────────────────────
    # Each cell ~ 0.20" wide x 0.14" tall
    fig_w = max(18, n_bars * 0.20 + 2)
    fig_h = max(10, n_prices * 0.16 + 3)

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor="#0d1117")
    gs = gridspec.GridSpec(
        2, 2,
        figure=fig,
        height_ratios=[5, 1],
        width_ratios=[30, 1],
        hspace=0.06,
        wspace=0.02,
    )
    ax_fp = fig.add_subplot(gs[0, 0])
    ax_cb = fig.add_subplot(gs[0, 1])
    ax_delta = fig.add_subplot(gs[1, 0], sharex=ax_fp)

    for ax in [ax_fp, ax_cb, ax_delta]:
        ax.set_facecolor("#0d1117")

    # ── Heatmap (delta-colored) ───────────────────────────────────────────────
    z = delta_piv.values.astype(float)
    max_abs = max(np.abs(z).max(), 1)
    norm = TwoSlopeNorm(vcenter=0, vmin=-max_abs, vmax=max_abs)

    im = ax_fp.imshow(
        z,
        aspect="auto",
        cmap=RdYlGn,
        norm=norm,
        origin="upper",
        extent=[-0.5, n_bars - 0.5, n_prices - 0.5, -0.5],
        alpha=0.7,
        interpolation="nearest",
    )

    # Colorbar
    cb = fig.colorbar(im, cax=ax_cb)
    cb.set_label("Delta (ask−bid)", color="#aaaaaa", fontsize=8)
    cb.ax.yaxis.set_tick_params(color="#aaaaaa", labelcolor="#aaaaaa", labelsize=7)

    # ── Cell text: bid×ask (only where total >= TEXT_MIN_VOL) ────────────────
    cell_fontsize = max(4.0, min(7.0, 90.0 / max(n_bars, n_prices, 1)))
    bid_arr = bid_piv.values
    ask_arr = ask_piv.values
    for i in range(n_prices):
        for j in range(n_bars):
            bv = int(bid_arr[i, j])
            av = int(ask_arr[i, j])
            if bv + av < TEXT_MIN_VOL:
                continue
            txt = f"{bv}×{av}"   # bid×ask  (×)
            # Dark text on light cells, white on dark cells
            delta_val = z[i, j]
            txt_color = "#000000" if abs(delta_val) > max_abs * 0.4 else "#cccccc"
            ax_fp.text(
                j, i, txt,
                ha="center", va="center",
                fontsize=cell_fontsize,
                color=txt_color,
                fontweight="bold" if max(bv, av) > 50 else "normal",
                clip_on=True,
            )

    # ── Adam's level (horizontal) ─────────────────────────────────────────────
    level_i = next(
        (i for i, p in enumerate(price_rows) if abs(p - level) < ES_TICK / 2),
        None,
    )
    if level_i is not None:
        ax_fp.axhline(y=level_i, color="#FFD700", linewidth=1.8, linestyle="--", alpha=0.9)
    else:
        # Level outside zoom; draw at nearest edge
        nearest = min(range(n_prices), key=lambda i: abs(price_rows[i] - level))
        ax_fp.axhline(y=nearest, color="#FFD700", linewidth=1.2, linestyle=":", alpha=0.6)

    # ── Call timestamp (vertical) ─────────────────────────────────────────────
    call_bar = post_ts.floor(f"{BAR_MINUTES}min")
    if call_bar in bar_times:
        call_j = bar_times.index(call_bar)
    else:
        diffs = [abs((b - call_bar).total_seconds()) for b in bar_times]
        call_j = int(np.argmin(diffs))

    ax_fp.axvline(x=call_j, color="#FFFFFF", linewidth=1.5, linestyle=":", alpha=0.85)

    # Annotation arrow at the call level
    arrow_level_i = level_i if level_i is not None else n_prices // 2
    dir_sym = "▲ LONG" if call["direction"] == "long" else "▼ SHORT"
    offset_j = max(2, int(n_bars * 0.05))
    offset_i = max(2, int(n_prices * 0.08))
    ax_fp.annotate(
        f"{dir_sym}\n{post_ts.strftime('%H:%M')} UTC",
        xy=(call_j, arrow_level_i),
        xytext=(
            min(call_j + offset_j, n_bars - 1),
            max(arrow_level_i - offset_i, 0),
        ),
        fontsize=8,
        color="#FFD700",
        fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#FFD700", lw=1.5),
        bbox=dict(boxstyle="round,pad=0.2", facecolor="#0d1117", alpha=0.7, edgecolor="#FFD700"),
    )

    # ── Y-axis: price labels ──────────────────────────────────────────────────
    step_y = max(1, n_prices // 20)
    ax_fp.set_yticks(range(0, n_prices, step_y))
    ax_fp.set_yticklabels(
        [f"{price_rows[i]:.2f}" for i in range(0, n_prices, step_y)],
        fontsize=7,
        color="#aaaaaa",
    )
    ax_fp.set_ylabel("Price (/ES)", color="#aaaaaa", fontsize=9)
    ax_fp.tick_params(axis="x", labelbottom=False)

    # ── Bottom panel: bar delta ───────────────────────────────────────────────
    bar_delta = fp_zoom.groupby("bar")["delta"].sum().reindex(bar_times, fill_value=0)
    bar_colors = ["#26a641" if d >= 0 else "#da3633" for d in bar_delta.values]
    ax_delta.bar(range(n_bars), bar_delta.values, color=bar_colors, width=0.85)
    ax_delta.axhline(0, color="#555555", linewidth=0.5)
    ax_delta.axvline(x=call_j, color="#FFFFFF", linewidth=1.5, linestyle=":", alpha=0.85)
    ax_delta.set_ylabel("Δ vol", color="#aaaaaa", fontsize=8)
    ax_delta.tick_params(axis="y", labelsize=6, labelcolor="#aaaaaa")

    # ── X-axis: time labels (shared) ─────────────────────────────────────────
    step_x = max(1, n_bars // 20)
    ax_delta.set_xticks(range(0, n_bars, step_x))
    ax_delta.set_xticklabels(
        [bar_times[j].strftime("%H:%M") for j in range(0, n_bars, step_x)],
        rotation=45,
        ha="right",
        fontsize=7,
        color="#aaaaaa",
    )
    ax_delta.tick_params(axis="x", colors="#aaaaaa")

    # ── Title ─────────────────────────────────────────────────────────────────
    pnl_str = f"+{call['points_net']:.2f}" if call["points_net"] > 0 else f"{call['points_net']:.2f}"
    pnl_color = "#26a641" if call["points_net"] > 0 else "#da3633"
    title = (
        f"Footprint — [{call['label']}]  "
        f"{call['direction'].upper()} @ {level}  |  "
        f"{post_ts.strftime('%Y-%m-%d %H:%M UTC')}  |  "
        f"P&L: {pnl_str} pts  ({call['exit_reason']})  |  "
        f"±{DISPLAY_PRICE_RANGE:.0f} pts  |  {BAR_MINUTES}-min bars  |  "
        f"green=ask-dom  red=bid-dom"
    )
    fig.suptitle(title, color=pnl_color, fontsize=9, fontweight="bold", y=0.995)

    # ── Save ──────────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(
        out_path,
        dpi=130,
        bbox_inches="tight",
        facecolor="#0d1117",
        edgecolor="none",
    )
    plt.close(fig)
    print(f"    saved: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Ensure stdout handles Unicode on Windows cp1252 consoles
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 70)
    print("FOOTPRINT VIEWER -- Adam's /ES Calls  (TBBO from Databento)")
    print("=" * 70)

    # Step 1: select
    calls = select_calls(CSV_PATH)
    print("\nStep 1 — Selected calls (from adam_trigger_study.csv):")
    for c in calls:
        sign = "+" if c["points_net"] > 0 else ""
        print(
            f"  [{c['label']:6s}]  post_ts={c['post_ts']}  "
            f"dir={c['direction']:5s}  level={c['level']}  "
            f"pts_net={sign}{c['points_net']}"
        )

    total_cost = 0.0
    png_paths: list[str] = []
    outcomes: list[dict] = []

    for call in calls:
        print(f"\n{'-'*60}")
        print(f"Processing [{call['label']}]: {call['post_ts']}  {call['direction']} @ {call['level']}")

        # Step 2+3: pull
        try:
            df, cost, start, end = pull_window(call)
        except RuntimeError as err:
            print(f"  ABORT: {err}")
            continue
        total_cost += cost

        # Step 4: build footprint
        fp = build_footprint(df)
        if fp.empty:
            print("  WARNING: no trade records with recognized side — skipping")
            outcomes.append({"call": call, "png": None, "cost": cost, "note": "no trades"})
            continue

        # Quick stats for report
        trade_count = int(fp["total"].sum())
        ask_dom = int(fp["ask_vol"].sum())
        bid_dom = int(fp["bid_vol"].sum())
        net_delta = ask_dom - bid_dom

        # Render
        out_path = OUT_DIR / f"footprint_{call['post_id']}.png"
        render_footprint(fp, call, out_path)
        png_paths.append(str(out_path))
        outcomes.append(
            {
                "call": call,
                "png": str(out_path),
                "cost": cost,
                "trade_count": trade_count,
                "ask_vol": ask_dom,
                "bid_vol": bid_dom,
                "net_delta": net_delta,
            }
        )

    # ── Step 5: Report ────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("REPORT")
    print(f"{'='*70}")
    print(f"Total Databento spend (estimated): ${total_cost:.4f}")
    print()
    for o in outcomes:
        c = o["call"]
        sign = "+" if c["points_net"] > 0 else ""
        print(
            f"[{c['label']:6s}]  {c['direction'].upper()} @ {c['level']}  "
            f"pts_net={sign}{c['points_net']}  ({c['exit_reason']})"
        )
        if o.get("png"):
            print(f"  PNG:       {o['png']}")
            print(
                f"  Vol in window: {o['trade_count']:,} contracts  "
                f"ask_vol={o['ask_vol']:,}  bid_vol={o['bid_vol']:,}  "
                f"net_delta={o['net_delta']:+,}"
            )
        print()

    print("PNGs saved:")
    for p in png_paths:
        print(f"  {p}")

    print("""
Visual read guide (1-min bars; finer resolution is Phase 2):
  ABSORPTION (Pattern A): heavy bid-vol (red) at the level but price holds or
    rallies. Shows buyers absorbing the supply — bullish for longs.
    For shorts: heavy ask-vol (green) at resistance with price rejecting.
  EXHAUSTION (Pattern B): decaying volume into the level — volume dries up
    as price approaches. Less conviction; often precedes fakeouts.
  Look at the cells on the call bar (white vertical line) and the
  2-3 bars before it. The yellow dashed line is Adam's stated level.
  Note: 1-min cells are coarse. Many trades within a bar collapse to one
  cell — intrabar microstructure (tick-level) is lost. Finer bins later.
""")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # Ensure stdout can handle Unicode (needed on Windows cp1252 consoles)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
