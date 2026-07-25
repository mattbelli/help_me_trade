#!/usr/bin/env python3
"""Measure the option bid-ask in VOL POINTS, so it compares directly with a VRP edge.

Spec: `References/OPTION_SPREAD_GATE_PREREG_2026-07-25.md`.

Study C found a +0.0445 vol-point premium across 2,083 corpus names and -0.0035 on the 37
we trade, at nearly identical implied vol. Every one of those numbers is vendor MID-market
IV and pays no spread. A seller sells at the bid, so the question is what survives.

    half_spread_vol_pts = 0.5 * (ask - bid) / vega        # vega per 1.00 of vol

which is the cost of crossing ONCE. Sell-to-open and hold to expiry pays it once; buying the
position back pays it twice.

    python tools/option_spread_probe.py --dry-run       # cohorts + coverage, no vendor calls
    python tools/option_spread_probe.py --limit 60      # 60 band names + the deep tier
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.alpaca import AlpacaAdapter  # noqa: E402
from signals.options_chain_snapshot import is_optionable_symbol, parse_occ  # noqa: E402

logger = logging.getLogger("option_spread_probe")
ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "options_iv_history.db"

TARGET_DTE = 30
DTE_LO, DTE_HI = 18, 45          # the window a ~30-day seller would actually use
STRIKE_BAND = 0.10               # +/-10% of spot is plenty to find the ATM strike
IV_CEILING = 0.60                # Study C's B4/C4 ceiling
EDGE_VOL_PTS = 0.0445            # Study C's C4 result, the thing we are measuring against
CHEAP_MAX, ERODED_MAX = 0.010, 0.022
MIN_COHORT = 20                  # below this a cohort is UNMEASURED, not a number
PROBE_PAUSE = 0.02


def band_cohort(limit: int | None) -> tuple[list[str], dict]:
    """Names carrying the premium NOW: top IV decile of the latest session, under the ceiling."""
    conn = sqlite3.connect(f"file:{CORPUS}?mode=ro", uri=True)
    latest = conn.execute("SELECT MAX(date) FROM volatility_history").fetchone()[0]
    rows = conn.execute(
        "SELECT act_symbol, iv_current FROM volatility_history WHERE date=?", (latest,)
    ).fetchall()
    conn.close()

    ivs = []
    for sym, raw in rows:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 0 and is_optionable_symbol(sym):
            ivs.append((sym, v))
    if not ivs:
        return [], {"session": latest, "cross_section": 0}

    # Order matters and C4 fixes it: restrict to the ceiling FIRST, then take the top
    # decile of what remains. The other order is empty here — the latest session's true
    # top decile starts at 0.77, well above anything wheelable.
    under = [(s, v) for s, v in ivs if v < IV_CEILING]
    if not under:
        return [], {"session": latest, "cross_section": len(ivs), "under_ceiling": 0}
    cutoff = sorted(v for _, v in under)[int(0.9 * len(under))]
    band = sorted(s for s, v in under if v >= cutoff)
    meta = {"session": latest, "cross_section": len(ivs), "under_ceiling": len(under),
            "top_decile_cutoff": cutoff, "in_band": len(band),
            "mean_iv_in_band": statistics.fmean(v for _, v in under if v >= cutoff)}
    return (band[:limit] if limit else band), meta


def deep_cohort() -> list[str]:
    from tools.options_chain_sampler import CORE_ROOTS, all_watchlist_symbols_fresh

    return sorted({*CORE_ROOTS, *[s for s in all_watchlist_symbols_fresh() if is_optionable_symbol(s)]})


def _pick_atm(snaps: dict, spot: float, today: date) -> dict:
    """Nearest expiry to TARGET_DTE, then the call and put whose strike is nearest spot."""
    by_expiry: dict[date, list] = {}
    for occ, snap in snaps.items():
        c = parse_occ(occ)
        if c is None:
            continue
        dte = (c.expiration - today).days
        if DTE_LO <= dte <= DTE_HI:
            by_expiry.setdefault(c.expiration, []).append((c, snap, dte))
    if not by_expiry:
        return {}
    expiry = min(by_expiry, key=lambda e: abs(by_expiry[e][0][2] - TARGET_DTE))
    out = {}
    for kind in ("call", "put"):
        legs = [(c, s, d) for c, s, d in by_expiry[expiry] if c.opt_type == kind]
        if legs:
            out[kind] = min(legs, key=lambda t: abs(t[0].strike - spot))
    return out


def _measure(contract, snap, dte: int, spot: float) -> dict | None:
    """Half-spread in vol points, or None with a reason — never an imputed number."""
    q = snap.get("latestQuote") or {}
    bid, ask = q.get("bp"), q.get("ap")
    vega = (snap.get("greeks") or {}).get("vega")
    if not bid or not ask or ask <= bid:
        return None
    if not vega or vega <= 0:
        return None
    # Vendor vega is per 1% of vol (verified against closed-form ATM below), so scale to 1.00.
    vega_per_vol = float(vega) * 100.0
    mid = (bid + ask) / 2
    return {
        "strike": contract.strike, "expiration": contract.expiration, "dte": dte,
        "bid": bid, "ask": ask, "mid": mid, "iv": snap.get("impliedVolatility"),
        "vega": float(vega), "vega_per_vol": vega_per_vol,
        "half_spread_vol_pts": 0.5 * (ask - bid) / vega_per_vol,
        "rel_spread": (ask - bid) / mid,
        "closed_form_atm_vega": 0.3989 * spot * math.sqrt(dte / 365) / 100.0,
        "quote_ts": q.get("t"),
    }


def probe(adapter: AlpacaAdapter, symbols: list[str], today: date) -> tuple[list[dict], dict]:
    lo = (today + timedelta(days=DTE_LO)).isoformat()
    hi = (today + timedelta(days=DTE_HI)).isoformat()
    spots: dict[str, float] = {}
    for start in range(0, len(symbols), 50):
        chunk = symbols[start : start + 50]
        try:
            bars = adapter.get_stock_bars_batch(chunk, timeframe="1Day", days=7) or {}
        except Exception:
            bars = {}
        for sym, rows in bars.items():
            if rows:
                spots[sym] = rows[-1].close

    out: list[dict] = []
    drop = {"no_spot": 0, "no_chain": 0, "no_dte_window": 0, "unquotable": 0}
    for i, sym in enumerate(symbols, 1):
        spot = spots.get(sym)
        if not spot:
            drop["no_spot"] += 1
            continue
        try:
            snaps = adapter.get_option_chain_snapshot(
                sym, limit=500, max_pages=3,
                expiration_date_gte=lo, expiration_date_lte=hi,
                strike_price_gte=spot * (1 - STRIKE_BAND),
                strike_price_lte=spot * (1 + STRIKE_BAND),
            )
        except Exception:
            snaps = {}
        if not snaps:
            drop["no_chain"] += 1
            continue
        picked = _pick_atm(snaps, spot, today)
        if not picked:
            drop["no_dte_window"] += 1
            continue
        legs = {}
        for kind, (c, snap, dte) in picked.items():
            m = _measure(c, snap, dte, spot)
            if m:
                legs[kind] = m
        if not legs:
            drop["unquotable"] += 1
            continue
        out.append({"symbol": sym, "spot": spot, "legs": legs,
                    "half_spread_vol_pts": statistics.fmean(
                        v["half_spread_vol_pts"] for v in legs.values())})
        if i % 25 == 0:
            logger.info("  probed %d/%d, %d usable", i, len(symbols), len(out))
        time.sleep(PROBE_PAUSE)
    return out, drop


def summarise(rows: list[dict], label: str) -> dict:
    if len(rows) < MIN_COHORT:
        return {"cohort": label, "n": len(rows), "status": "UNMEASURED",
                "reason": f"fewer than {MIN_COHORT} usable symbols"}
    vals = sorted(r["half_spread_vol_pts"] for r in rows)
    med = statistics.median(vals)
    verdict = "CHEAP" if med <= CHEAP_MAX else ("ERODED" if med <= ERODED_MAX else "PROHIBITIVE")
    return {
        "cohort": label, "n": len(rows), "status": "measured", "verdict": verdict,
        "median_half_spread_vol_pts": med,
        "mean_half_spread_vol_pts": statistics.fmean(vals),
        "p25": vals[len(vals) // 4], "p75": vals[3 * len(vals) // 4],
        "worst": vals[-1], "best": vals[0],
        "median_share_of_edge": med / EDGE_VOL_PTS,
        "round_trip_exceeds_edge": bool(2 * med > EDGE_VOL_PTS),
        "median_rel_spread": statistics.median(
            statistics.fmean(v["rel_spread"] for v in r["legs"].values()) for r in rows),
    }


def vega_convention_check(rows: list[dict]) -> dict:
    """Verify vendor vega is per-1% rather than assuming it — a wrong scale moves every number 100x."""
    ratios = []
    for r in rows:
        for v in r["legs"].values():
            if v["closed_form_atm_vega"] > 0:
                ratios.append(v["vega"] / v["closed_form_atm_vega"])
    if not ratios:
        return {"checked": 0, "consistent": False}
    med = statistics.median(ratios)
    return {"checked": len(ratios), "median_vendor_over_closed_form": med,
            "consistent": bool(0.5 <= med <= 1.5)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=80, help="band symbols to probe (default 80)")
    ap.add_argument("--dry-run", action="store_true", help="cohorts only, no vendor calls")
    ap.add_argument("--out", default=str(ROOT / "data" / "research" / "option_spread_gate"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    band, meta = band_cohort(args.limit)
    deep = deep_cohort()
    logger.info("band cohort: %d of %d in band (session %s, top-decile cutoff %.4f, ceiling %.2f)",
                len(band), meta.get("in_band", 0), meta["session"], meta.get("top_decile_cutoff", 0), IV_CEILING)
    logger.info("deep cohort: %d symbols", len(deep))
    if args.dry_run:
        logger.info("DRY RUN. band sample: %s", ", ".join(band[:20]))
        return 0

    adapter = AlpacaAdapter.from_settings()
    logging.getLogger("brokers.alpaca").setLevel(logging.ERROR)
    today = date.today()

    logger.info("probing BAND ...")
    band_rows, band_drop = probe(adapter, band, today)
    logger.info("probing DEEP ...")
    deep_rows, deep_drop = probe(adapter, deep, today)

    result = {
        "spec": "References/OPTION_SPREAD_GATE_PREREG_2026-07-25.md",
        "edge_vol_pts": EDGE_VOL_PTS, "cohort_meta": meta,
        "band": summarise(band_rows, "BAND (top IV decile, IV < 0.60)"),
        "deep": summarise(deep_rows, "DEEP (names we trade)"),
        "band_dropped": band_drop, "deep_dropped": deep_drop,
        "vega_convention": vega_convention_check(band_rows + deep_rows),
        "quote_sample_ts": [r["legs"][k]["quote_ts"] for r in band_rows[:3] for k in r["legs"]],
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=2, default=str))
    (out / "rows.json").write_text(json.dumps({"band": band_rows, "deep": deep_rows}, indent=2, default=str))

    vc = result["vega_convention"]
    print(f"\nvega convention: vendor/closed-form median {vc.get('median_vendor_over_closed_form', float('nan')):.3f} "
          f"over {vc['checked']} contracts -> {'OK (per-1%)' if vc['consistent'] else 'MISMATCH — run invalid'}")
    for key in ("band", "deep"):
        s = result[key]
        print(f"\n{s['cohort']}: n={s['n']}")
        if s["status"] != "measured":
            print(f"  {s['status']} — {s['reason']}")
            continue
        print(f"  median half-spread {s['median_half_spread_vol_pts']:.4f} vol pts "
              f"({s['median_share_of_edge']:.0%} of the {EDGE_VOL_PTS:.4f} edge) -> {s['verdict']}")
        print(f"  p25 {s['p25']:.4f}  p75 {s['p75']:.4f}  best {s['best']:.4f}  worst {s['worst']:.4f}")
        print(f"  median relative spread {s['median_rel_spread']:.1%} of premium; "
              f"round trip exceeds edge: {s['round_trip_exceeds_edge']}")
    print(f"\ndropped band={band_drop} deep={deep_drop}")
    print(f"wrote {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
