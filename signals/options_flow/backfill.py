"""One-shot historical backfill for ``options_flow_daily`` + thresholds doc.

Read-only, offline, manual CLI (not a job). Uses Alpaca ``get_option_contracts``
+ ``get_option_bars`` (1Day) back to ~2024-02-12 so UV-2 baselines have history
on day one. See design §4 ("Backfill for threshold tuning").

Honesty caveats (also written into ``research/options_flow/thresholds.md``):
- History is only ~2.5 yr (≈ one bull regime).
- Only currently-listed contracts are enumerable (mild survivorship tilt).
- Historical per-day OI is unavailable → ``close_oi`` left NULL in backfilled rows.
"""

from __future__ import annotations

import argparse
import logging
import math
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from db import options_flow as of_db
from signals.options_flow.constants import (
    DTE_BUCKETS,
    OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING,
    dte_bucket,
)

logger = logging.getLogger(__name__)

_BARS_CHUNK = 100
_UPSERT_BATCH = 500
_DEFAULT_START = "2024-02-12"
_DEFAULT_DOC = Path("research/options_flow/thresholds.md")
# Fraction of the median per-(underlying, bucket) std used as ZSCORE_STD_FLOOR.
# Thin names with near-zero trailing std otherwise fire UV-2 on tiny absolute moves.
_STD_FLOOR_FRACTION = 0.1


def _int_oi(v: Any) -> int | None:
    if v is None:
        return None
    try:
        s = str(v).strip()
        if not s:
            return None
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _normalize_right(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s.startswith("c"):
        return "call"
    if s.startswith("p"):
        return "put"
    return s or "call"


def _dte_days(expiration: str, bar_date: str) -> int | None:
    try:
        exp = date.fromisoformat(str(expiration).strip()[:10])
        day = date.fromisoformat(str(bar_date).strip()[:10])
        return (exp - day).days
    except (TypeError, ValueError):
        return None


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolation percentile; ``p`` in [0, 100]. Empty → 0.0."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    if p <= 0:
        return float(sorted_vals[0])
    if p >= 100:
        return float(sorted_vals[-1])
    rank = (p / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(sorted_vals[lo])
    w = rank - lo
    return float(sorted_vals[lo]) * (1.0 - w) + float(sorted_vals[hi]) * w


def distribution_stats(values: list[float]) -> dict[str, float | int]:
    """p50/p90/p95/p99 + mean/std/n for a list of daily bucket volumes."""
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return {
            "n": 0,
            "mean": 0.0,
            "std": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
        }
    sorted_vals = sorted(vals)
    mean = float(statistics.mean(vals))
    std = float(statistics.stdev(vals)) if n > 1 else 0.0
    return {
        "n": n,
        "mean": mean,
        "std": std,
        "p50": _percentile(sorted_vals, 50),
        "p90": _percentile(sorted_vals, 90),
        "p95": _percentile(sorted_vals, 95),
        "p99": _percentile(sorted_vals, 99),
    }


def recommend_zscore_std_floor(
    stats_by_key: dict[tuple[str, str], dict[str, float | int]],
    *,
    fraction: float = _STD_FLOOR_FRACTION,
) -> float:
    """Recommend ``ZSCORE_STD_FLOOR`` as ``fraction`` × median bucket std.

    UV-2 divides by trailing std; thin names with near-zero std produce huge
    z-scores on tiny absolute volume moves. Flooring at a small fraction of the
    typical (median) bucket std kills those false fires without muting liquid
    names whose natural std is large.
    """
    stds = [
        float(s["std"])
        for s in stats_by_key.values()
        if int(s.get("n") or 0) > 1 and float(s.get("std") or 0.0) > 0
    ]
    if not stds:
        return 0.0
    return float(fraction) * float(statistics.median(stds))


def aggregate_daily_bucket_volumes(
    rows: list[dict],
) -> dict[tuple[str, str], list[float]]:
    """Sum contract volume per (underlying, dte_bucket, date); drop ``out``."""
    day_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in rows:
        bucket = str(row.get("dte_bucket") or "")
        if bucket == "out" or not bucket:
            continue
        u = str(row.get("underlying") or "").strip().upper()
        day = str(row.get("date") or "").strip()[:10]
        if not u or not day:
            continue
        try:
            vol = float(row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        day_totals[(u, bucket, day)] += vol

    out: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (u, bucket, _day), total in sorted(day_totals.items()):
        out[(u, bucket)].append(total)
    return dict(out)


def compute_distributions(
    rows: list[dict],
) -> dict[tuple[str, str], dict[str, float | int]]:
    """Per (underlying, dte_bucket) distribution of daily total bucket volume."""
    volumes = aggregate_daily_bucket_volumes(rows)
    return {key: distribution_stats(vals) for key, vals in volumes.items()}


def _rank_contracts(contracts: list[dict], cap: int) -> list[dict]:
    """Keep top ``cap`` by open_interest desc, then nearer expiry."""
    ranked: list[tuple[int, str, dict]] = []
    for c in contracts:
        if not isinstance(c, dict):
            continue
        sym = str(c.get("symbol") or "").strip().upper()
        if not sym:
            continue
        oi = _int_oi(c.get("open_interest"))
        oi_rank = oi if oi is not None else -1
        exp = str(c.get("expiration_date") or "").strip()[:10] or "9999-99-99"
        ranked.append((oi_rank, exp, c))
    ranked.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in ranked[:cap]]


def _bar_to_row(
    underlying: str,
    occ: str,
    contract: dict,
    bar: dict,
) -> dict | None:
    """Build one ``options_flow_daily`` row from a 1Day bar, or None to skip."""
    if not isinstance(bar, dict):
        return None
    t = bar.get("t")
    if not t:
        return None
    bar_date = str(t).strip()[:10]
    try:
        vol_raw = bar.get("v")
        if vol_raw is None:
            return None
        volume = int(float(vol_raw))
    except (TypeError, ValueError):
        return None
    if volume <= 0:
        return None
    try:
        close = float(bar.get("c"))
    except (TypeError, ValueError):
        return None

    expiry = str(contract.get("expiration_date") or "").strip()[:10]
    dte = _dte_days(expiry, bar_date)
    if dte is None or dte < 0:
        return None

    right = _normalize_right(contract.get("type"))
    bucket = dte_bucket(dte)
    premium = float(volume) * close * 100.0
    return {
        "underlying": underlying.strip().upper(),
        "occ_symbol": occ,
        "date": bar_date,
        "right": right,
        "dte": dte,
        "dte_bucket": bucket,
        "volume": volume,
        "prints": None,
        "premium_usd": premium,
        # Historical per-day OI is not available from Alpaca; leave NULL.
        "close_oi": None,
    }


def backfill_underlying(
    adapter: Any,
    underlying: str,
    start: str,
    *,
    db_path: Path | None = None,
    contract_cap: int | None = None,
) -> list[dict]:
    """Fetch contracts + 1Day bars for one underlying; upsert daily rows.

    Returns the daily rows built (also written via ``upsert_daily`` in batches).
    """
    u = underlying.strip().upper()
    cap = (
        OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING
        if contract_cap is None
        else int(contract_cap)
    )
    contracts_raw = adapter.get_option_contracts(u)
    if not isinstance(contracts_raw, list):
        contracts_raw = []
    ranked = _rank_contracts(contracts_raw, cap)
    if not ranked:
        logger.info("options_flow backfill: %s — no contracts", u)
        return []

    meta_by_occ: dict[str, dict] = {}
    for c in ranked:
        occ = str(c.get("symbol") or "").strip().upper()
        if occ:
            meta_by_occ[occ] = c

    occ_list = list(meta_by_occ.keys())
    bars_by_occ: dict[str, list[dict]] = {}
    for i in range(0, len(occ_list), _BARS_CHUNK):
        chunk = occ_list[i : i + _BARS_CHUNK]
        got = adapter.get_option_bars(chunk, start=start, timeframe="1Day")
        if isinstance(got, dict):
            for occ, items in got.items():
                bars_by_occ[str(occ).strip().upper()] = items

    daily_rows: list[dict] = []
    for occ, contract in meta_by_occ.items():
        for bar in bars_by_occ.get(occ) or []:
            row = _bar_to_row(u, occ, contract, bar)
            if row is not None:
                daily_rows.append(row)

    written = 0
    for i in range(0, len(daily_rows), _UPSERT_BATCH):
        batch = daily_rows[i : i + _UPSERT_BATCH]
        written += of_db.upsert_daily(batch, db_path=db_path)

    logger.info(
        "options_flow backfill: %s contracts=%s rows=%s upserted=%s",
        u,
        len(ranked),
        len(daily_rows),
        written,
    )
    return daily_rows


def write_thresholds_doc(
    path: Path,
    stats_by_key: dict[tuple[str, str], dict[str, float | int]],
    zscore_std_floor: float,
    *,
    start: str,
    symbols: list[str],
    n_rows: int,
) -> None:
    """Write ``thresholds.md`` with percentile table, floor, and honesty caveats."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    known_buckets = [f"{lo}-{hi}" for lo, hi in DTE_BUCKETS]
    lines: list[str] = [
        "# Options-flow thresholds (backfill-derived)",
        "",
        f"Generated by `signals/options_flow/backfill.py` from 1Day option bars "
        f"starting `{start}` for symbols: `{', '.join(symbols)}` "
        f"({n_rows} daily contract-rows).",
        "",
        "## Honesty caveats",
        "",
        "- **Regime coverage:** history is only ~2.5 years (≈ one bull regime). "
        "These percentiles tune UV-2 scale; they are not multi-regime validated "
        "parameters.",
        "- **Survivorship tilt:** only *currently-listed* contracts are enumerable "
        "from Alpaca's contracts endpoint. Expired contracts that traded in the "
        "window are missing — a mild tilt on the baseline, acceptable because "
        "baselines only set the z-score scale.",
        "- **No historical OI:** Alpaca exposes current open interest only. "
        "Backfilled `options_flow_daily.close_oi` rows are left **NULL**; "
        "vol/OI (UV-1) cannot be reconstructed from this backfill.",
        "",
        "## Recommended `ZSCORE_STD_FLOOR`",
        "",
        f"**`ZSCORE_STD_FLOOR = {zscore_std_floor:.6g}`**",
        "",
        f"Derived as `{_STD_FLOOR_FRACTION}` × the median of per-(underlying, "
        f"DTE-bucket) daily-volume standard deviations across the backfill. "
        "UV-2 uses `z = (vol − mean) / max(std, floor)`. Without a floor, thin "
        "names whose trailing 20-day std collapses near zero produce huge z-scores "
        "on tiny absolute volume moves (false fires). A small fraction of the "
        "typical bucket std stops that without muting liquid names.",
        "",
        "## Per-day total bucket volume — percentiles",
        "",
        "For each (underlying, DTE bucket), volumes are summed across contracts "
        "per calendar day, then the distribution of those daily totals is "
        "summarized.",
        "",
        "| underlying | dte_bucket | n_days | mean | std | p50 | p90 | p95 | p99 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def _fmt(x: float | int) -> str:
        if isinstance(x, int):
            return str(x)
        if abs(float(x)) >= 1000:
            return f"{float(x):.1f}"
        return f"{float(x):.4g}"

    keys = sorted(
        stats_by_key.keys(),
        key=lambda k: (k[0], known_buckets.index(k[1]) if k[1] in known_buckets else 99),
    )
    for (u, bucket) in keys:
        s = stats_by_key[(u, bucket)]
        lines.append(
            f"| {u} | {bucket} | {int(s['n'])} | {_fmt(s['mean'])} | {_fmt(s['std'])} "
            f"| {_fmt(s['p50'])} | {_fmt(s['p90'])} | {_fmt(s['p95'])} | {_fmt(s['p99'])} |"
        )

    if not keys:
        lines.append("| _(none)_ | — | 0 | — | — | — | — | — | — |")

    lines.extend(["", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_backfill(
    adapter: Any,
    *,
    symbols: list[str],
    start: str = _DEFAULT_START,
    db_path: Path | None = None,
    write_doc: bool = True,
    doc_path: Path | None = None,
    contract_cap: int | None = None,
) -> dict:
    """Backfill all symbols (fail-soft); optionally write thresholds doc.

    Returns a summary dict with row counts, distributions, and recommended floor.
    """
    syms = [s.strip().upper() for s in symbols if s and str(s).strip()]
    all_rows: list[dict] = []
    n_ok = 0
    n_fail = 0

    for sym in syms:
        try:
            rows = backfill_underlying(
                adapter,
                sym,
                start,
                db_path=db_path,
                contract_cap=contract_cap,
            )
            all_rows.extend(rows)
            n_ok += 1
        except Exception:
            n_fail += 1
            logger.exception("options_flow backfill: skip underlying %s", sym)
            continue

    stats = compute_distributions(all_rows)
    floor = recommend_zscore_std_floor(stats)

    if write_doc:
        out = Path(doc_path) if doc_path is not None else _DEFAULT_DOC
        write_thresholds_doc(
            out,
            stats,
            floor,
            start=start,
            symbols=syms,
            n_rows=len(all_rows),
        )
        logger.info("options_flow backfill: wrote %s", out)

    summary_line = (
        f"backfill done: symbols_ok={n_ok} failed={n_fail} rows={len(all_rows)} "
        f"buckets={len(stats)} ZSCORE_STD_FLOOR~={floor:.6g}"
    )
    print(summary_line)
    return {
        "symbols_ok": n_ok,
        "symbols_failed": n_fail,
        "daily_rows": len(all_rows),
        "distributions": stats,
        "zscore_std_floor": floor,
        "summary": summary_line,
        "rows": all_rows,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "One-shot backfill of options_flow_daily from Alpaca 1Day option bars "
            "and write research/options_flow/thresholds.md"
        )
    )
    p.add_argument(
        "--symbols",
        default="",
        help="Comma-separated underlyings (required; no auto-watchlist)",
    )
    p.add_argument(
        "--start",
        default=_DEFAULT_START,
        help=f"Bars start date YYYY-MM-DD (default {_DEFAULT_START})",
    )
    p.add_argument(
        "--db-path",
        default=None,
        help="Optional SQLite path (default: app options_flow db)",
    )
    p.add_argument(
        "--doc-path",
        default=None,
        help=f"Thresholds markdown path (default {_DEFAULT_DOC})",
    )
    p.add_argument(
        "--no-doc",
        action="store_true",
        help="Skip writing thresholds.md",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: require --symbols, Alpaca ``get_option_bars``, then backfill."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    symbols = [s.strip() for s in str(args.symbols).split(",") if s.strip()]
    if not symbols:
        print(
            "error: pass --symbols SPY,AAPL,... "
            "(this script does not auto-pull a live watchlist)",
            file=sys.stderr,
        )
        return 2

    try:
        from brokers.factory import get_broker

        broker = get_broker("alpaca")  # options data lives only on Alpaca
    except Exception as exc:
        print(f"error: Alpaca broker unavailable: {exc}", file=sys.stderr)
        return 1

    if not hasattr(broker, "get_option_bars"):
        print(
            "error: broker has no get_option_bars "
            "(options-flow backfill requires Alpaca); exiting",
            file=sys.stderr,
        )
        return 1

    db_path = Path(args.db_path) if args.db_path else None
    doc_path = Path(args.doc_path) if args.doc_path else None
    run_backfill(
        broker,
        symbols=symbols,
        start=args.start,
        db_path=db_path,
        write_doc=not args.no_doc,
        doc_path=doc_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
