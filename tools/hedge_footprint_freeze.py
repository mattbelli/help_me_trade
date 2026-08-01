"""3.3 label freeze — `hedge_scores_frozen_3p3.json` (FLOW_ROADMAP.md 3.3 SPEC v2).

Writes the registered frozen-label artifact BEFORE any outcome fetch: one row per
event in 3.2's quiet/primary/64g cut, carrying the stratum (HIGH/NULL/ANTI), the
label variants for the stability preconditions, and every registered covariate
computable from the already-unblinded ±300s windows + metadata. NO forward price
is read here — outcomes stay sealed. The file is serialized deterministically and
its SHA-256 is what the registration block pins; `sha256` of the canonical bytes
is printed on every run so a re-run can prove reproduction.

Companion output `hedge_balance_3p3.json`: the per-stratum balance table the spec
disclosed at freeze (medians only — no outcome touches this either).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.earnings_calendar import earnings_dates_for  # noqa: E402
from tools.hedge_footprint import (  # noqa: E402
    G_NS,
    PRIMARY_HORIZON_G,
    _arrays,
    _load_window,
    _signed_arrays,
    build_condition_table,
    excess,
    hedge_aligned,
    measure_event,
    nbbo_mid_at,
    prevailing_quote_idx,
    window_sum,
)

logger = logging.getLogger("hedge_footprint_freeze")

HIGH_THRESHOLD = 3.0  # registered: HIGH s >= +3, ANTI s <= -3, NULL between
EARNINGS_LOOKAHEAD_DAYS = 45  # store dates in (session, session+45d]; fences cut later
MARK_OFFSET_NS = 300 * 1_000_000_000  # M0 = T+300s
LATE_LO_NS = 240 * 1_000_000_000  # late-window excess covariate [T+240s, T+300s)
STALE_QUOTE_S = 60.0  # registered staleness rule threshold, recorded per event


def stratum_for(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "HIGH"
    if score <= -HIGH_THRESHOLD:
        return "ANTI"
    return "NULL"


def otm_pct(strike: float, spot: float, is_put: bool) -> float:
    """% out-of-the-money at the measurement spot; negative = ITM."""
    if not (math.isfinite(spot) and spot > 0.0):
        return float("nan")
    return (strike - spot) / spot if not is_put else (spot - strike) / spot


def _aligned_z(rec: dict, horizon_g: int, arm: str) -> float:
    z = rec["horizons"][str(horizon_g)][arm]["z"]
    return float(z * rec["h_sign"]) if np.isfinite(z) else float("nan")


def _mark_covariates(out_dir: Path, symbol: str, t0_ns: int, table: dict,
                     control_ts_ns: list[int], h_sign: float) -> dict:
    """m (aligned mid move over the score window), late-window aligned excess,
    and the M0 quote-staleness fields — all inside the unblinded ±300s data."""
    loaded = _load_window(out_dir, symbol, t0_ns)
    if loaded is None:
        return {"m_bps": None, "late_excess_z": None,
                "m0_quote_age_s": None, "m0_stale": None}
    trades, quotes = loaded
    mid_t = nbbo_mid_at(quotes, t0_ns)
    mid_300 = nbbo_mid_at(quotes, t0_ns + MARK_OFFSET_NS)
    m_bps = (h_sign * math.log(mid_300 / mid_t) * 1e4
             if mid_t > 0 and mid_300 > 0 else float("nan"))

    idx = prevailing_quote_idx(
        np.array([t0_ns + MARK_OFFSET_NS], dtype=np.int64), quotes["t_ns"])[0]
    age_s = ((t0_ns + MARK_OFFSET_NS - int(quotes["t_ns"][idx])) / 1e9
             if idx >= 0 else float("nan"))

    ev_arr = _signed_arrays(trades, quotes, table)
    s_ev = window_sum(ev_arr["t"], ev_arr["primary"],
                      t0_ns + LATE_LO_NS, t0_ns + MARK_OFFSET_NS)
    s_ct = []
    for ts in control_ts_ns:
        c = _load_window(out_dir, symbol, ts)
        if c is None:
            continue
        c_arr = _signed_arrays(*c, table)
        s_ct.append(window_sum(c_arr["t"], c_arr["primary"],
                               ts + LATE_LO_NS, ts + MARK_OFFSET_NS))
    late_z = (excess(s_ev, np.array(s_ct, dtype=np.float64)).z * h_sign
              if len(s_ct) >= 3 else float("nan"))

    def _num(v: float) -> float | None:
        return round(float(v), 6) if np.isfinite(v) else None

    return {"m_bps": _num(m_bps), "late_excess_z": _num(late_z),
            "m0_quote_age_s": _num(age_s),
            "m0_stale": bool(age_s > STALE_QUOTE_S) if np.isfinite(age_s) else None}


def build_rows(out_dir: Path, stratum_name: str) -> list[dict]:
    table = build_condition_table()
    ledger = json.loads((out_dir / f"fetch_ledger_{stratum_name}.json").read_text())
    by_key = {(ev["symbol"], ev["ts_ns"]): ev for ev in ledger["events"]}
    records = [r for ev in ledger["events"]
               if (r := measure_event(out_dir, ev, table)) is not None]
    usable = [r for r in records if not r.get("dropped")]
    cut = _arrays(usable, PRIMARY_HORIZON_G, tier="quiet", band="primary")
    logger.info("cut: %d events (quiet/primary/%dg)", len(cut["records"]),
                PRIMARY_HORIZON_G)

    rows = []
    for rec, score in zip(cut["records"],
                          hedge_aligned(cut["value"], cut["h_sign"]), strict=True):
        ev = by_key[(rec["symbol"], rec["ts_ns"])]
        session = date.fromisoformat(rec["session"])
        horizon_end = session + timedelta(days=EARNINGS_LOOKAHEAD_DAYS)
        earnings = [d for d in earnings_dates_for(rec["symbol"])
                    if session.isoformat() < d <= horizon_end.isoformat()]
        spot = float(rec["spot_measure"])
        row = {
            "symbol": rec["symbol"],
            "session": rec["session"],
            "ts_ns": int(rec["ts_ns"]),
            "instrument_id": int(ev["instrument_id"]),
            "root": ev["root"],
            "strike": float(ev["strike"]),
            "is_put": bool(ev["is_put"]),
            "side_sign": int(ev["side_sign"]),
            "contracts": int(rec["contracts"]),
            "premium": float(rec["premium"]),
            # ledger `exp_days` is the expiry as EPOCH days (see expiry_ns); `dte`
            # is the days-to-expiry number the spec's balance table wants
            "dte": int(ev["dte"]),
            "expiry_date": (date(1970, 1, 1)
                            + timedelta(days=int(ev["exp_days"]))).isoformat(),
            "h_sign": float(rec["h_sign"]),
            "abs_h": round(float(rec["abs_h"]), 2),
            "d": round(float(rec["d"]), 4),
            "delta_measure": round(float(rec["delta_measure"]), 6),
            "spot_measure": round(spot, 6),
            "otm_pct": round(otm_pct(float(ev["strike"]), spot, bool(ev["is_put"])), 6),
            "score": round(float(score), 6),
            "stratum": stratum_for(float(score)),
            "cluster": f"{rec['symbol']}|{rec['session']}",
            "score_trf_incl": round(_aligned_z(rec, PRIMARY_HORIZON_G, "trf_incl"), 6),
            "score_tick": round(_aligned_z(rec, PRIMARY_HORIZON_G, "tick"), 6),
            "score_16g": round(_aligned_z(rec, 16, "primary"), 6),
            "score_165g": round(_aligned_z(rec, 165, "primary"), 6),
            "n_controls": int(rec["n_controls"]),
            "earnings_dates_45d": earnings,
            **_mark_covariates(out_dir, rec["symbol"], int(rec["ts_ns"]), table,
                               ev["control_ts_ns"], float(rec["h_sign"])),
        }
        rows.append(row)
    rows.sort(key=lambda r: (r["symbol"], r["ts_ns"]))
    return rows


def canonical_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def balance_table(rows: list[dict]) -> dict:
    out: dict = {}
    for stratum in ("HIGH", "NULL", "ANTI"):
        sub = [r for r in rows if r["stratum"] == stratum]
        if not sub:
            continue
        med = {k: float(np.median([r[k] for r in sub if r[k] is not None]))
               for k in ("premium", "contracts", "d", "abs_h", "spot_measure",
                         "otm_pct", "dte", "m_bps")}
        out[stratum] = {
            "n_events": len(sub),
            "n_underlyings": len({r["symbol"] for r in sub}),
            "medians": {k: round(v, 4) for k, v in med.items()},
            "mean_h_sign": round(float(np.mean([r["h_sign"] for r in sub])), 4),
            "n_puts": sum(r["is_put"] for r in sub),
            "n_customer_buy": sum(r["side_sign"] > 0 for r in sub),
            "n_earnings_within_3d": sum(
                any(d <= (date.fromisoformat(r["session"])
                          + timedelta(days=3)).isoformat()
                    for d in r["earnings_dates_45d"]) for r in sub),
            "n_earnings_within_9d": sum(
                any(d <= (date.fromisoformat(r["session"])
                          + timedelta(days=9)).isoformat()
                    for d in r["earnings_dates_45d"]) for r in sub),
        }
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path,
                    default=Path("data/research/hedge_footprint/equity"))
    ap.add_argument("--stratum", default="equity")
    args = ap.parse_args()

    rows = build_rows(args.out_dir, args.stratum)
    doc = {
        "spec": "FLOW_ROADMAP.md 3.3 SPEC v2 (REGISTERED 2026-08-01)",
        "high_threshold": HIGH_THRESHOLD,
        "horizon_g": PRIMARY_HORIZON_G,
        "grain_ns": G_NS,
        "n_events": len(rows),
        "events": rows,
    }
    payload = canonical_bytes(doc)
    sha = hashlib.sha256(payload).hexdigest()

    frozen_path = args.out_dir / "hedge_scores_frozen_3p3.json"
    if frozen_path.exists():
        existing = frozen_path.read_bytes()
        if hashlib.sha256(existing).hexdigest() != sha:
            logger.error("FROZEN FILE MISMATCH: on-disk %s != recomputed %s — the "
                         "label file is registered; investigate, never overwrite",
                         hashlib.sha256(existing).hexdigest()[:16], sha[:16])
            return 1
        logger.info("frozen file reproduced exactly: sha256=%s", sha)
    else:
        frozen_path.write_bytes(payload)
        logger.info("frozen file WRITTEN: sha256=%s", sha)

    bal = balance_table(rows)
    (args.out_dir / "hedge_balance_3p3.json").write_text(json.dumps(bal, indent=1))
    counts = {s: bal.get(s, {}).get("n_events", 0) for s in ("HIGH", "NULL", "ANTI")}
    logger.info("strata: %s | balance table written", counts)
    print(f"SHA256 {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
