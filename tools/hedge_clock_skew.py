#!/usr/bin/env python3
"""Phase 3.1 — cross-tape clock-skew bound via scheduled-release burst alignment.

Roadmap item 3.1 (spec registered 2026-08-01 in References/FLOW_ROADMAP.md). The
option side is settled — the 07-27 join probe measured Databento tcbbo `ts_event`
vs Alpaca option `t` at exactly 0ns p1–p99 (both pass the OPRA participant
timestamp). The open half is the EQUITY tape: Alpaca `/v2/stocks/{sym}/trades`
exposes one timestamp `t`, and participant-vs-SIP is exactly the skew Phase 3.2
inherits.

Method: a scheduled macro release (FOMC 14:00:00 ET decision, ISM 10:00:00 ET) is
a physical common event on a NIST-locked clock. Measure burst onset on the SPY
equity tape (Alpaca snapshot fetch) and the SPY option tape (session tcbbo) with
the SAME registered rule; the onset delta Δ = option − equity, across events, is
the skew distribution — an upper bound at the grain where both tapes react.

Read-only market data; no orders; nothing under trading/. Snapshot method: one
[T−60s, T+10s] window per event, nothing bulk, $0.

    .venv/bin/python tools/hedge_clock_skew.py                # all events
    .venv/bin/python tools/hedge_clock_skew.py --events fomc  # primary set only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import alpaca_config  # noqa: E402

logger = logging.getLogger("hedge_clock_skew")

_ET = ZoneInfo("America/New_York")
DATA_DIR = ROOT / "data" / "databento"
OUT_DIR = ROOT / "data" / "research" / "hedge_clock_skew"

# Registered onset rule (roadmap 3.1 spec, 2026-08-01).
BASELINE_S = 60
HORIZON_S = 10
BIN_MS = 100
BURST_MULT = 5.0
MIN_TRADES = 5

PAGE_LIMIT = 10_000
# Same pagination discipline as underlying_trades_fetch.py — follow
# next_page_token or silently keep page 1 (the stale-candle bug class).
MAX_PAGES = 200

# FOMC decision days inside the corpus span, 14:00:00 ET embargo — primary set.
# All 9 tcbbo sessions verified on disk 2026-08-01.
FOMC_DATES = (
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-10",
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
)
# ISM manufacturing, 10:00:00 ET, first business day of the month — secondary
# set, weaker bursts allowed to fail (the NO-BURST guard reports, never invents).
ISM_DATES = (
    "2025-08-01",
    "2025-09-02",
    "2025-10-01",
    "2025-11-03",
    "2025-12-01",
    "2026-01-02",
    "2026-02-02",
    "2026-03-02",
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
)

TRF_EXCHANGE = "D"  # FINRA TRF prints: reporting-firm clock, broken out separately


def event_t0_ns(session: date, hhmm: str) -> int:
    """Release time as epoch-ns, DST-honest via zoneinfo."""
    t = dtime.fromisoformat(hhmm)
    return int(datetime.combine(session, t, tzinfo=_ET).timestamp() * 1e9)


def parse_t_ns(value: str) -> int:
    """RFC3339 with up to ns precision -> epoch ns, no float round-trip."""
    return int(np.datetime64(str(value).rstrip("Z"), "ns").astype(np.int64))


def onset(
    ts_ns: np.ndarray,
    t0_ns: int,
    baseline_s: int = BASELINE_S,
    horizon_s: int = HORIZON_S,
    bin_ms: int = BIN_MS,
    mult: float = BURST_MULT,
    min_trades: int = MIN_TRADES,
) -> dict:
    """Registered burst-onset rule, identical for both tapes.

    Baseline = trades-per-bin over [t0−baseline, t0); onset bin = first bin in
    [t0, t0+horizon) with count ≥ max(mult × baseline, min_trades); onset = first
    trade timestamp inside that bin. No qualifying bin ⇒ burst=False (NO-BURST).
    """
    ts_ns = np.asarray(ts_ns, dtype=np.int64)
    bin_ns = bin_ms * 1_000_000
    base_lo = t0_ns - baseline_s * 1_000_000_000
    base = ts_ns[(ts_ns >= base_lo) & (ts_ns < t0_ns)]
    n_base_bins = baseline_s * 1000 // bin_ms
    baseline_per_bin = len(base) / n_base_bins
    thresh = max(mult * baseline_per_bin, float(min_trades))

    hi = t0_ns + horizon_s * 1_000_000_000
    horizon = ts_ns[(ts_ns >= t0_ns) & (ts_ns < hi)]
    result = {
        "burst": False,
        "onset_ns": None,
        "onset_ms": None,
        "first_trade_ms": None,
        "baseline_per_bin": round(baseline_per_bin, 3),
        "threshold": round(thresh, 3),
        "n_baseline": int(len(base)),
        "n_horizon": int(len(horizon)),
        "profile_first_3s": [],
    }
    if len(horizon) == 0:
        return result
    result["first_trade_ms"] = round(float(horizon.min() - t0_ns) / 1e6, 3)
    idx = (horizon - t0_ns) // bin_ns
    counts = np.bincount(idx.astype(np.int64), minlength=horizon_s * 1000 // bin_ms)
    result["profile_first_3s"] = counts[: 3000 // bin_ms].tolist()
    hits = np.nonzero(counts >= thresh)[0]
    if len(hits) == 0:
        return result
    first_bin = int(hits[0])
    in_bin = horizon[idx == first_bin]
    onset_ns = int(in_bin.min())
    result.update(
        burst=True,
        onset_ns=onset_ns,
        onset_ms=round((onset_ns - t0_ns) / 1e6, 3),
    )
    return result


def granularity(ts_ns: np.ndarray) -> dict:
    """Sub-second digit forensics — a ms-truncated feed is a skew floor."""
    ts_ns = np.asarray(ts_ns, dtype=np.int64)
    n = len(ts_ns)
    if n == 0:
        return {"n": 0}
    return {
        "n": int(n),
        "sub_us_digits_pct": round(float(np.mean(ts_ns % 1_000 != 0)), 4),
        "us_multiple_pct": round(float(np.mean(ts_ns % 1_000 == 0)), 4),
        "ms_multiple_pct": round(float(np.mean(ts_ns % 1_000_000 == 0)), 4),
        "s_multiple_pct": round(float(np.mean(ts_ns % 1_000_000_000 == 0)), 4),
    }


def fetch_equity_trades(
    client: httpx.Client,
    data_base_url: str,
    symbol: str,
    start_ns: int,
    end_ns: int,
    feed: str | None = None,
) -> dict[str, np.ndarray]:
    """One snapshot window of stock trades, keeping t/price/size/exchange."""
    params: dict[str, str | int] = {
        "start": f"{np.datetime64(start_ns, 'ns')}Z",
        "end": f"{np.datetime64(end_ns, 'ns')}Z",
        "limit": PAGE_LIMIT,
        "sort": "asc",
    }
    if feed:
        params["feed"] = feed
    t_ns: list[int] = []
    price: list[float] = []
    size: list[int] = []
    exch: list[str] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        q = dict(params)
        if page_token:
            q["page_token"] = page_token
        resp = client.get(f"{data_base_url}/v2/stocks/{symbol}/trades", params=q)
        if resp.status_code != 200:
            raise RuntimeError(
                f"GET /v2/stocks/{symbol}/trades failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )
        payload = resp.json()
        for tr in payload.get("trades") or []:
            t_ns.append(parse_t_ns(tr["t"]))
            price.append(float(tr["p"]))
            size.append(int(tr["s"]))
            exch.append(str(tr.get("x") or ""))
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    else:
        logger.warning("pagination capped at %d pages for %s — TRUNCATED", MAX_PAGES, symbol)
    order = np.argsort(np.array(t_ns, dtype=np.int64), kind="stable")
    return {
        "t_ns": np.array(t_ns, dtype=np.int64)[order],
        "price": np.array(price, dtype=np.float64)[order],
        "size": np.array(size, dtype=np.int64)[order],
        "exchange": np.array(exch, dtype="U2")[order],
    }


def spy_option_ts(session: date, data_dir: Path, lo_ns: int, hi_ns: int) -> np.ndarray:
    """SPY option print timestamps from the session tcbbo, via that day's defs."""
    import databento as dbn

    dpath = data_dir / "opra_defs" / f"{session.isoformat()}.dbn.zst"
    tpath = data_dir / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    for p in (dpath, tpath):
        if not p.exists():
            raise FileNotFoundError(p)
    defs = dbn.DBNStore.from_file(dpath).to_ndarray()
    cls = defs["instrument_class"]
    is_opt = (cls == b"C") | (cls == b"P")
    und = np.char.strip(defs["underlying"].astype("U16"))
    ids = np.unique(defs["instrument_id"][is_opt & (und == "SPY")].astype(np.int64))
    if len(ids) == 0:
        raise RuntimeError(f"no SPY option defs on {session}")
    arr = dbn.DBNStore.from_file(tpath).to_ndarray()
    ts = arr["ts_event"].astype(np.int64)
    sel = (ts >= lo_ns) & (ts < hi_ns)
    keep = np.isin(arr["instrument_id"][sel].astype(np.int64), ids)
    return np.sort(ts[sel][keep])


def measure_event(
    session: date,
    hhmm: str,
    label: str,
    client: httpx.Client,
    data_base_url: str,
    data_dir: Path,
    feed: str | None,
    out_dir: Path,
) -> dict:
    t0 = event_t0_ns(session, hhmm)
    lo = t0 - BASELINE_S * 1_000_000_000
    hi = t0 + HORIZON_S * 1_000_000_000
    row: dict = {"date": session.isoformat(), "time_et": hhmm, "label": label}

    try:
        opt_ts = spy_option_ts(session, data_dir, lo, hi)
    except FileNotFoundError as exc:
        row["status"] = f"no tcbbo/defs on disk: {Path(str(exc)).name}"
        return row

    eq = fetch_equity_trades(client, data_base_url, "SPY", lo, hi, feed)
    # Persist the raw snapshot so re-analysis never refetches.
    out_dir.mkdir(parents=True, exist_ok=True)
    snap = out_dir / f"eq_{session.isoformat()}_{hhmm.replace(':', '')}.npz"
    tmp = snap.with_suffix(".npz.part")
    with open(tmp, "wb") as fh:  # file handle: savez appends ".npz" to bare paths
        np.savez_compressed(fh, **eq)
    tmp.replace(snap)

    is_trf = eq["exchange"] == TRF_EXCHANGE
    row["equity"] = onset(eq["t_ns"][~is_trf], t0)
    row["option"] = onset(opt_ts, t0)
    row["granularity"] = {
        "equity_exchange": granularity(eq["t_ns"][~is_trf]),
        "equity_trf": granularity(eq["t_ns"][is_trf]),
        "option": granularity(opt_ts),
    }
    row["trf_share"] = round(float(np.mean(is_trf)), 4) if len(is_trf) else None
    if row["equity"]["burst"] and row["option"]["burst"]:
        row["delta_ms"] = round(row["option"]["onset_ms"] - row["equity"]["onset_ms"], 3)
        row["status"] = "ok"
    else:
        row["delta_ms"] = None
        sides = [s for s in ("equity", "option") if not row[s]["burst"]]
        row["status"] = f"NO-BURST ({', '.join(sides)})"
    return row


def summarize(rows: list[dict]) -> dict:
    """The Δ distribution + the registered grain recommendation for 3.2."""

    def dist(deltas: list[float]) -> dict | None:
        if not deltas:
            return None
        a = np.array(deltas, dtype=np.float64)
        return {
            "n": len(a),
            "min_ms": float(a.min()),
            "p50_ms": float(np.percentile(a, 50)),
            "p90_abs_ms": float(np.percentile(np.abs(a), 90)),
            "max_ms": float(a.max()),
        }

    fomc = [r["delta_ms"] for r in rows if r.get("delta_ms") is not None and r["label"] == "fomc"]
    every = [r["delta_ms"] for r in rows if r.get("delta_ms") is not None]
    out = {"fomc": dist(fomc), "all": dist(every)}
    d = out["fomc"] or out["all"]
    if d:
        # Registered decision rule: grain = max(100ms, p90|Δ|); the measured
        # granularity floor is ≤1ms on every feed seen, so it cannot bind here,
        # but a |Δ| over 1s is a STOP.
        out["min_leadlag_grain_ms"] = max(100.0, d["p90_abs_ms"])
        out["stop_investigate"] = bool(max(abs(x) for x in (every or fomc)) > 1000.0)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", choices=("fomc", "ism", "all"), default="all")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    events: list[tuple[str, str, str]] = []
    if args.events in ("fomc", "all"):
        events += [(d, "14:00", "fomc") for d in FOMC_DATES]
    if args.events in ("ism", "all"):
        events += [(d, "10:00", "ism") for d in ISM_DATES]

    cfg = alpaca_config()
    if not cfg["api_key_id"]:
        raise SystemExit("no Alpaca credentials in settings/.env")
    headers = {
        "APCA-API-KEY-ID": cfg["api_key_id"],
        "APCA-API-SECRET-KEY": cfg["api_secret_key"],
    }
    feed = cfg["data_feed"] if cfg["data_feed"] != "auto" else None

    rows: list[dict] = []
    with httpx.Client(headers=headers, timeout=60.0) as client:
        for iso, hhmm, label in events:
            row = measure_event(
                date.fromisoformat(iso), hhmm, label, client,
                cfg["data_base_url"], args.data_dir, feed, args.out_dir,
            )
            rows.append(row)
            eq_ms = row.get("equity", {}).get("onset_ms")
            op_ms = row.get("option", {}).get("onset_ms")
            logger.info(
                "%s %s [%s] equity=%sms option=%sms delta=%sms %s",
                iso, hhmm, label, eq_ms, op_ms, row.get("delta_ms"), row["status"],
            )

    result = {
        "spec": "FLOW_ROADMAP.md 3.1 (registered 2026-08-01)",
        "onset_rule": {
            "baseline_s": BASELINE_S, "horizon_s": HORIZON_S, "bin_ms": BIN_MS,
            "mult": BURST_MULT, "min_trades": MIN_TRADES,
        },
        "events": rows,
        "summary": summarize(rows),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / "results.json"
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(result, indent=1))
    tmp.replace(path)
    logger.info("summary: %s", json.dumps(result["summary"]))
    logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
