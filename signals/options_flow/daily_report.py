"""Post-close flow decomposition — what was the gauge's premium actually made of?

Pure analysis (rolling.py discipline — no I/O, no clocks): callers feed print-lab
rows and gauge day summaries; this module clusters prints into orders, splits
premium by lane (index complex vs single names) and tenor, measures
concentration, and emits rule-based characterizations. Every characterization is
phrased "consistent with", never asserted as fact — the rules are heuristics,
not validated signals (presentation honesty per the connect-the-dots rule).

Born 2026-07-17: the gauge's first live day read net-bear into an open that
ripped. Decomposition showed the bear premium was a handful of long-tenor SPX
blocks — institutional hedge rolls on OpEx morning, not directional flow. This
module makes that decomposition happen mechanically every day.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from signals.options_flow.constants import INDEX_MEGACAP_SET
from signals.options_flow.opening_pressure import PRESSURE_UNDERLYINGS, window_minute
from signals.options_flow.rolling import CLUSTER_PRINT_MIN_USD

_ET = ZoneInfo("America/New_York")

# Order clustering: prints on the same contract, same side, same price within the
# same second are one parent order swept across exchanges (the 07-17 SPX 800-lot
# printed as 8×100 in one second).
_CLUSTER_SECOND_MS = 1000

# Tenor: DTE ≥ 31 aligns with the design §5 UV-2 buckets (31-90, 91+). Portfolio
# hedges/rolls live out here; 0-1 DTE is where index directional day flow lives.
LONG_TENOR_MIN_DTE = 31
TENOR_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0, 1, "0-1d"),
    (2, 7, "2-7d"),
    (8, 30, "8-30d"),
    (31, 90, "31-90d"),
    (91, 10_000, "91d+"),
)

# Characterization rule thresholds (heuristics, named so they're auditable).
HEDGE_TOP_N = 3  # "a handful of blocks"
HEDGE_TOP_SHARE_MIN = 0.40  # top-3 orders carry ≥40% of the side
HEDGE_LONG_TENOR_SHARE_MIN = 0.60  # and the side is ≥60% long-tenor premium
SIDE_MIN_USD = 5_000_000  # only characterize sides big enough to move the net
DIVERGENCE_MIN_USD = 2_000_000  # SPX-vs-SPY sign disagreement floor per side
STANDOUT_NET_MIN_USD = 5_000_000  # single-name callout floor
STANDOUT_RATIO_MIN = 3.0  # and at least 3:1 side dominance

TOP_ORDERS_SHOWN = 8

WINDOW_LABELS = {"open": "09:30-10:00 ET", "close": "15:30-16:00 ET"}

# Standing caveats — rendered on every report, never dropped (07-08 red-team:
# honesty flags must survive every transport).
CAVEATS: tuple[str, ...] = (
    "Direction is aggressor-side (who crossed the spread), not market direction — "
    "put buying is often hedging, and dealers hedging sold puts BUY futures.",
    "Multi-leg structures print leg by leg: a put leg of a bullish spread or "
    "collar classifies as 'bear'. Legs the stream flags as spreads are excluded "
    "from the gauge but not always detectable.",
    "Raw premium sums, uncalibrated — whether net premium predicts, anti-predicts, "
    "or means nothing is exactly what the baseline accrual will measure.",
    "Coverage is the subscribed gauge lane (index 0DTE = top contracts by $vol), "
    "not the full OPRA tape.",
    "Characterizations are rule-based heuristics ('consistent with'), not "
    "validated classifications of who traded or why.",
)


def _occ_right(occ: str) -> str:
    """'c' / 'p' from an OSI symbol (letter before the 8-digit strike), '' if odd."""
    if len(occ) >= 9 and occ[-9] in ("C", "P"):
        return occ[-9].lower()
    return ""


def _stance(direction: str, right: str) -> str:
    """Gauge mapping: bull = call buys + put sells; bear = put buys + call sells."""
    if direction == "buy_side" and right == "c":
        return "bull"
    if direction == "sell_side" and right == "p":
        return "bull"
    if direction == "buy_side" and right == "p":
        return "bear"
    if direction == "sell_side" and right == "c":
        return "bear"
    return "unclassified"


def _tenor_bucket(dte: int) -> str:
    for lo, hi, label in TENOR_BUCKETS:
        if lo <= dte <= hi:
            return label
    return TENOR_BUCKETS[-1][2]


def cluster_orders(prints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group prints into parent orders: same (contract, side, price, second).

    Input rows are print-lab records (ts_ms, occ, u, price, size, dte, dir).
    Only side-classified prints above the gauge's dust floor participate.
    Returns orders sorted by premium descending.
    """
    groups: dict[tuple, dict[str, Any]] = {}
    for p in prints:
        # Sparse capture flags (present since 2026-07-18): spread legs and
        # hygiene-excluded prints match the gauge's own exclusions. Older
        # capture days lack the flags — the report's basis note covers them.
        if p.get("spread") or p.get("hyg"):
            continue
        try:
            ts_ms = int(p["ts_ms"])
            price = float(p["price"])
            size = int(p["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0 or size <= 0:
            continue
        prem = price * size * 100.0
        if prem < CLUSTER_PRINT_MIN_USD:
            continue
        direction = str(p.get("dir") or "undetermined")
        occ = str(p.get("occ") or "")
        right = _occ_right(occ)
        stance = _stance(direction, right)
        if stance == "unclassified":
            continue
        key = (occ, direction, round(price, 4), ts_ms // _CLUSTER_SECOND_MS)
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "occ": occ,
                "underlying": str(p.get("u") or ""),
                "right": right,
                "dir": direction,
                "stance": stance,
                "price": price,
                "contracts": size,
                "premium_usd": prem,
                "dte": int(p.get("dte") or 0),
                "n_prints": 1,
                "ts_ms": ts_ms,
            }
        else:
            g["contracts"] += size
            g["premium_usd"] += prem
            g["n_prints"] += 1
            g["ts_ms"] = min(g["ts_ms"], ts_ms)
    orders = sorted(groups.values(), key=lambda g: -g["premium_usd"])
    for o in orders:
        o["time_et"] = datetime.fromtimestamp(o["ts_ms"] / 1000, tz=_ET).strftime(
            "%H:%M:%S"
        )
    return orders


def analyze_window(prints: list[dict[str, Any]], window: str) -> dict[str, Any]:
    """Order-level decomposition of one gauge window from print-lab rows."""
    in_window = [
        p
        for p in prints
        if str(p.get("u") or "") in PRESSURE_UNDERLYINGS
        and isinstance(p.get("ts_ms"), (int, float))
        and window_minute(int(p["ts_ms"]), window) is not None
    ]
    orders = cluster_orders(in_window)

    lanes: dict[str, dict[str, float]] = {
        "index": {"bull_usd": 0.0, "bear_usd": 0.0},
        "single_name": {"bull_usd": 0.0, "bear_usd": 0.0},
    }
    tenor: dict[str, dict[str, float]] = {
        label: {"bull_usd": 0.0, "bear_usd": 0.0} for _, _, label in TENOR_BUCKETS
    }
    long_tenor: dict[str, dict[str, float]] = {
        "index": {"bull_usd": 0.0, "bear_usd": 0.0},
        "single_name": {"bull_usd": 0.0, "bear_usd": 0.0},
    }
    for o in orders:
        lane = "index" if o["underlying"] in INDEX_MEGACAP_SET else "single_name"
        side_key = f"{o['stance']}_usd"
        lanes[lane][side_key] += o["premium_usd"]
        tenor[_tenor_bucket(o["dte"])][side_key] += o["premium_usd"]
        if o["dte"] >= LONG_TENOR_MIN_DTE:
            long_tenor[lane][side_key] += o["premium_usd"]

    classified_total = sum(v for lane in lanes.values() for v in lane.values())
    top10 = sum(o["premium_usd"] for o in orders[:10])
    return {
        "window": window,
        "window_et": WINDOW_LABELS.get(window, window),
        "orders": orders,
        "top_orders": [
            {k: v for k, v in o.items() if k != "ts_ms"}
            for o in orders[:TOP_ORDERS_SHOWN]
        ],
        "lanes": {
            lane: {
                **vals,
                "net_usd": vals["bull_usd"] - vals["bear_usd"],
                "long_tenor_bull_usd": long_tenor[lane]["bull_usd"],
                "long_tenor_bear_usd": long_tenor[lane]["bear_usd"],
            }
            for lane, vals in lanes.items()
        },
        "tenor": [
            {"bucket": label, **tenor[label]} for _, _, label in TENOR_BUCKETS
        ],
        "concentration": {
            "n_orders": len(orders),
            "n_prints": len(in_window),
            "classified_usd": classified_total,
            "top10_share": (top10 / classified_total) if classified_total > 0 else 0.0,
        },
    }


def _fmt_musd(v: float) -> str:
    return f"${v / 1e6:,.1f}M"


def characterize(
    analysis: dict[str, Any], *, is_opex: bool
) -> list[dict[str, str]]:
    """Rule-based reads of one window's decomposition. Each has a `code` naming
    the rule and text phrased as 'consistent with' — heuristic, not verdict."""
    out: list[dict[str, str]] = []
    window = analysis["window"]
    orders = analysis["orders"]

    # R1 — hedge-tenor block concentration, PER index underlying and side. The
    # per-underlying scale is where the signature lives (measured 2026-07-17:
    # SPX open bear = top-3 71% / long-tenor 100% while SPY/QQQ's broad
    # short-dated flow sat at top-3 6-16% / long-tenor 4-6% — pooling the whole
    # index lane dilutes the blocks below every threshold). SPXW folds into SPX.
    by_name_side: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in orders:
        if o["underlying"] not in INDEX_MEGACAP_SET:
            continue
        name = "SPX" if o["underlying"] in ("SPX", "SPXW") else o["underlying"]
        by_name_side.setdefault((name, o["stance"]), []).append(o)
    for (name, side), side_orders in sorted(by_name_side.items()):
        side_total = sum(o["premium_usd"] for o in side_orders)
        if side_total < SIDE_MIN_USD:
            continue
        top = sorted(side_orders, key=lambda o: -o["premium_usd"])[:HEDGE_TOP_N]
        top_share = sum(o["premium_usd"] for o in top) / side_total
        long_share = (
            sum(o["premium_usd"] for o in side_orders if o["dte"] >= LONG_TENOR_MIN_DTE)
            / side_total
        )
        if top_share >= HEDGE_TOP_SHARE_MIN and long_share >= HEDGE_LONG_TENOR_SHARE_MIN:
            biggest = top[0]
            desc = (
                f"{biggest['contracts']:,}x {biggest['occ']} "
                f"({biggest['dte']}d) at {biggest['time_et']} ET"
            )
            text = (
                f"{top_share:.0%} of {name} {side} premium ({_fmt_musd(side_total)}) "
                f"came from {len(top)} long-tenor order(s) (≥{LONG_TENOR_MIN_DTE} DTE; "
                f"largest {desc}) — consistent with institutional "
                f"hedging/rebalancing, not short-dated directional positioning."
            )
            if is_opex:
                text += (
                    " Monthly OpEx: expiring protection commonly rolls to back "
                    "months today."
                )
            out.append({"code": "hedge_tenor_blocks", "window": window, "text": text})

    # R2 — SPX complex vs SPY sign disagreement (block-hedge venue vs ETF tape).
    spx_net = sum(
        o["premium_usd"] * (1 if o["stance"] == "bull" else -1)
        for o in orders
        if o["underlying"] in ("SPX", "SPXW")
    )
    spy_net = sum(
        o["premium_usd"] * (1 if o["stance"] == "bull" else -1)
        for o in orders
        if o["underlying"] == "SPY"
    )
    if (
        abs(spx_net) >= DIVERGENCE_MIN_USD
        and abs(spy_net) >= DIVERGENCE_MIN_USD
        and (spx_net > 0) != (spy_net > 0)
    ):
        out.append(
            {
                "code": "spx_spy_divergence",
                "window": window,
                "text": (
                    f"SPX complex net ({_fmt_musd(spx_net)}) and SPY net "
                    f"({_fmt_musd(spy_net)}) disagree in sign — SPX blocks skew "
                    "toward institutional hedges/rolls; don't read the pooled "
                    "index net as one voice."
                ),
            }
        )

    # R3 — single-name standout.
    per_name: dict[str, dict[str, float]] = {}
    for o in orders:
        if o["underlying"] in INDEX_MEGACAP_SET:
            continue
        d = per_name.setdefault(o["underlying"], {"bull": 0.0, "bear": 0.0})
        d[o["stance"]] += o["premium_usd"]
    for sym, d in sorted(
        per_name.items(), key=lambda kv: -abs(kv[1]["bull"] - kv[1]["bear"])
    ):
        net = d["bull"] - d["bear"]
        small, big = min(d["bull"], d["bear"]), max(d["bull"], d["bear"])
        if abs(net) >= STANDOUT_NET_MIN_USD and (
            small == 0 or big / small >= STANDOUT_RATIO_MIN
        ):
            side = "bull" if net > 0 else "bear"
            out.append(
                {
                    "code": "single_name_standout",
                    "window": window,
                    "text": (
                        f"{sym} stands out: net {side} {_fmt_musd(abs(net))} "
                        f"({_fmt_musd(d['bull'])} bull vs {_fmt_musd(d['bear'])} bear)."
                    ),
                }
            )
    return out
