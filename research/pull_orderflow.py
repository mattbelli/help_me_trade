"""Pull TBBO + MBO order-flow windows for all Adam /ES calls.

Windows:  post_ts − 1 h … post_ts + 1 h (2-hour window per call).
Output:   data/research/_edgehunt/orderflow/<post_id>_{tbbo,mbo}.parquet
Resumable: skips (post_id, schema) whose parquet already exists.

Budget guard (hard): total projected spend must be ≤ HARD_BUDGET ($70) or the
script aborts BEFORE pulling a single byte.  Expected: ~$59 (tbbo ~$27 + mbo ~$31).

Usage:
    python -m research.pull_orderflow               # full run — both schemas (spends credits)
    python -m research.pull_orderflow --dry-run     # estimate only, no pull
    python -m research.pull_orderflow --schema mbo  # MBO-only (~$49.59, under $70 guard)
    python -m research.pull_orderflow --schema mbo --dry-run  # estimate MBO cost, no pull
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

from research.databento_loader import estimate_cost, pull_mbo, pull_tbbo

# ── Config ────────────────────────────────────────────────────────────────────
HARD_BUDGET = 70.00       # USD — abort if projected total > this (non-negotiable)
RUNNING_GUARD = 68.00     # USD — stop mid-run if cumulative spend reaches this
SYMBOL = "ES.c.0"
WINDOW_HOURS = 1          # hours before/after post_ts → 2-hour windows
CSV_PATH = ROOT / "data/research/adam_trigger_study.csv"
OUT_DIR = ROOT / "data/research/_edgehunt/orderflow"
# ─────────────────────────────────────────────────────────────────────────────


def _window(post_ts_str: str) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a WINDOW_HOURS-wide window around post_ts."""
    ts = datetime.fromisoformat(post_ts_str)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%S"
    start = (ts - timedelta(hours=WINDOW_HOURS)).strftime(fmt)
    end = (ts + timedelta(hours=WINDOW_HOURS)).strftime(fmt)
    return start, end


def phase1_estimate(rows: list[dict], schemas: set[str]) -> list[dict]:
    """Estimate cost for every window × selected schemas (FREE metadata queries).

    Returns list of estimate dicts with keys:
        post_id, start, end, tbbo_cost, mbo_cost, tbbo_path, mbo_path
    """
    n = len(rows)
    n_queries = n * len(schemas)
    schema_label = "+".join(sorted(schemas))
    print(
        f"=== PHASE 1: Cost estimation (FREE) — {n} calls × {len(schemas)} schema(s)"
        f" ({schema_label}) = {n_queries} queries ==="
    )
    estimates: list[dict] = []

    for i, row in enumerate(rows, 1):
        post_id = str(row["post_id"])
        start, end = _window(row["post_ts"])
        tbbo_path = OUT_DIR / f"{post_id}_tbbo.parquet"
        mbo_path = OUT_DIR / f"{post_id}_mbo.parquet"

        tbbo_cost = (
            estimate_cost(symbol=SYMBOL, schema="tbbo", start=start, end=end)[0]
            if "tbbo" in schemas
            else 0.0
        )
        mbo_cost = (
            estimate_cost(symbol=SYMBOL, schema="mbo", start=start, end=end)[0]
            if "mbo" in schemas
            else 0.0
        )

        estimates.append(
            {
                "post_id": post_id,
                "start": start,
                "end": end,
                "tbbo_cost": tbbo_cost,
                "mbo_cost": mbo_cost,
                "tbbo_path": tbbo_path,
                "mbo_path": mbo_path,
            }
        )

        if i % 10 == 0 or i == n:
            running_t = sum(e["tbbo_cost"] for e in estimates)
            running_m = sum(e["mbo_cost"] for e in estimates)
            total = running_t + running_m
            if "tbbo" in schemas and "mbo" in schemas:
                print(
                    f"  [{i:3d}/{n}]  running tbbo=${running_t:.2f}"
                    f"  mbo=${running_m:.2f}  total=${total:.2f}"
                )
            elif "mbo" in schemas:
                print(f"  [{i:3d}/{n}]  running mbo=${running_m:.2f}")
            else:
                print(f"  [{i:3d}/{n}]  running tbbo=${running_t:.2f}")

    return estimates


def phase1_report(estimates: list[dict], schemas: set[str]) -> float:
    """Print cost summary and return projected total. Aborts if > HARD_BUDGET."""
    total_tbbo = sum(e["tbbo_cost"] for e in estimates) if "tbbo" in schemas else 0.0
    total_mbo = sum(e["mbo_cost"] for e in estimates) if "mbo" in schemas else 0.0
    total_projected = total_tbbo + total_mbo

    remaining_tbbo = (
        sum(e["tbbo_cost"] for e in estimates if not e["tbbo_path"].exists())
        if "tbbo" in schemas
        else 0.0
    )
    remaining_mbo = (
        sum(e["mbo_cost"] for e in estimates if not e["mbo_path"].exists())
        if "mbo" in schemas
        else 0.0
    )
    remaining = remaining_tbbo + remaining_mbo

    tbbo_already = sum(1 for e in estimates if e["tbbo_path"].exists()) if "tbbo" in schemas else 0
    mbo_already = sum(1 for e in estimates if e["mbo_path"].exists()) if "mbo" in schemas else 0

    print()
    print("=== PROJECTED COST SUMMARY ===")
    if "tbbo" in schemas:
        print(
            f"  TBBO total: ${total_tbbo:.2f}"
            f"  ({tbbo_already} files already exist → to pull: ${remaining_tbbo:.2f})"
        )
    if "mbo" in schemas:
        print(
            f"  MBO  total: ${total_mbo:.2f}"
            f"  ({mbo_already} files already exist → to pull: ${remaining_mbo:.2f})"
        )
    print(f"  GRAND TOTAL (selected schemas): ${total_projected:.2f}")
    print(f"  Remaining to pull:              ${remaining:.2f}")
    print(f"  HARD BUDGET:                    ${HARD_BUDGET:.2f}")
    print()

    if total_projected > HARD_BUDGET:
        print(
            f"*** BUDGET GUARD TRIGGERED ***\n"
            f"  Projected ${total_projected:.2f} > ${HARD_BUDGET:.2f} limit.\n"
            f"  Aborting WITHOUT pulling any data.\n"
            f"  Review windows or raise budget to proceed."
        )
        sys.exit(1)

    print(f"Budget guard PASSED (${total_projected:.2f} <= ${HARD_BUDGET:.2f}). Proceeding with pulls.")
    return total_projected


def phase2_pull(estimates: list[dict], schemas: set[str], dry_run: bool = False) -> dict:
    """Pull all missing windows for selected schemas. Returns stats dict."""
    print()
    if dry_run:
        print("=== PHASE 2: DRY RUN (no pulls) ===")
    else:
        print("=== PHASE 2: Pulling data (SPENDS CREDITS) ===")

    cumulative = 0.0
    stats = {"tbbo_pulled": 0, "mbo_pulled": 0, "tbbo_skipped": 0, "mbo_skipped": 0, "failed": []}
    n_total = len(estimates) * len(schemas)
    done = 0

    for est in estimates:
        post_id = est["post_id"]
        start, end = est["start"], est["end"]
        tbbo_path: Path = est["tbbo_path"]
        mbo_path: Path = est["mbo_path"]

        # Running guard
        if cumulative >= RUNNING_GUARD:
            print(f"\nRUNNING GUARD: cumulative spend ${cumulative:.2f} >= ${RUNNING_GUARD:.2f}. Stopping early.")
            break

        # ── TBBO ──────────────────────────────────────────────────────────────
        if "tbbo" in schemas:
            if tbbo_path.exists():
                stats["tbbo_skipped"] += 1
                done += 1
            elif dry_run:
                done += 1
            else:
                try:
                    # Per-window guard: 2× estimated cost + buffer
                    per_guard = est["tbbo_cost"] * 2.0 + 0.10
                    df_t, cost = pull_tbbo(start=start, end=end, symbol=SYMBOL, budget_usd=per_guard)
                    cumulative += cost
                    # Always write (empty df still produces valid parquet → skip on re-run)
                    df_t.to_parquet(tbbo_path, index=False)
                    done += 1
                    if df_t.empty:
                        stats["failed"].append({"post_id": post_id, "schema": "tbbo", "reason": "empty response"})
                        print(f"  [{done:3d}/{n_total}] TBBO {post_id[:16]}  EMPTY  ${cost:.4f} cumul=${cumulative:.2f}")
                    else:
                        stats["tbbo_pulled"] += 1
                        print(
                            f"  [{done:3d}/{n_total}] TBBO {post_id[:16]}  {len(df_t):>7,} rows"
                            f"  ${cost:.4f}  cumul=${cumulative:.2f}"
                        )
                except Exception as exc:
                    done += 1
                    stats["failed"].append({"post_id": post_id, "schema": "tbbo", "reason": str(exc)})
                    print(f"  [{done:3d}/{n_total}] TBBO {post_id[:16]}  ERROR: {exc}")

            # Running guard between schemas
            if cumulative >= RUNNING_GUARD:
                print(f"\nRUNNING GUARD: cumulative spend ${cumulative:.2f} >= ${RUNNING_GUARD:.2f}. Stopping early.")
                break

        # ── MBO ───────────────────────────────────────────────────────────────
        if "mbo" in schemas:
            if mbo_path.exists():
                stats["mbo_skipped"] += 1
                done += 1
            elif dry_run:
                done += 1
            else:
                try:
                    per_guard = est["mbo_cost"] * 2.0 + 0.10
                    df_m, cost = pull_mbo(start=start, end=end, symbol=SYMBOL, budget_usd=per_guard)
                    cumulative += cost
                    df_m.to_parquet(mbo_path, index=False)
                    done += 1
                    if df_m.empty:
                        stats["failed"].append({"post_id": post_id, "schema": "mbo", "reason": "empty response"})
                        print(f"  [{done:3d}/{n_total}] MBO  {post_id[:16]}  EMPTY  ${cost:.4f} cumul=${cumulative:.2f}")
                    else:
                        stats["mbo_pulled"] += 1
                        print(
                            f"  [{done:3d}/{n_total}] MBO  {post_id[:16]}  {len(df_m):>7,} rows"
                            f"  ${cost:.4f}  cumul=${cumulative:.2f}"
                        )
                except Exception as exc:
                    done += 1
                    stats["failed"].append({"post_id": post_id, "schema": "mbo", "reason": str(exc)})
                    print(f"  [{done:3d}/{n_total}] MBO  {post_id[:16]}  ERROR: {exc}")

    stats["cumulative_spend"] = cumulative
    return stats


def phase3_report(projected_total: float, stats: dict, schemas: set[str]) -> None:
    """Print final report and verify a sample file is loadable."""
    print()
    print("=== PHASE 3: FINAL REPORT ===")
    cumulative = stats["cumulative_spend"]
    print(f"  Projected total:  ${projected_total:.2f}")
    print(f"  Actual spend:     ${cumulative:.2f}  (delta ${projected_total - cumulative:+.2f})")
    if "tbbo" in schemas:
        print(f"  TBBO: {stats['tbbo_pulled']} pulled, {stats['tbbo_skipped']} skipped")
    if "mbo" in schemas:
        print(f"  MBO:  {stats['mbo_pulled']} pulled,  {stats['mbo_skipped']} skipped")

    failed = stats["failed"]
    if failed:
        print(f"  Failed/empty: {len(failed)}")
        for f in failed:
            print(f"    post_id={f['post_id'][:16]}  schema={f['schema']}  reason={f['reason']}")
    else:
        print("  No failures.")

    # Storage
    parquet_files = list(OUT_DIR.glob("*.parquet"))
    total_bytes = sum(p.stat().st_size for p in parquet_files)
    print(f"  Storage: {len(parquet_files)} files  {total_bytes / 1e9:.3f} GB under {OUT_DIR}")

    # Verify loadable: first non-empty file for each selected schema
    tbbo_files = sorted(p for p in parquet_files if "_tbbo" in p.name) if "tbbo" in schemas else []
    mbo_files = sorted(p for p in parquet_files if "_mbo" in p.name) if "mbo" in schemas else []

    print()
    print("  Loadability check:")
    for label, files in [("TBBO", tbbo_files), ("MBO", mbo_files)]:
        if not files:
            continue
        for fp in files:
            try:
                df = pd.read_parquet(fp)
                if not df.empty:
                    print(f"    {label} sample → {fp.name}: shape={df.shape}, cols={list(df.columns)}")
                    break
            except Exception as e:
                print(f"    {label} load error ({fp.name}): {e}")
        else:
            print(f"    {label}: no non-empty files found yet")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Estimate only — do not pull (no credits spent)")
    parser.add_argument(
        "--schema",
        choices=["both", "tbbo", "mbo"],
        default="both",
        help="Which schema(s) to estimate+pull (default: both). 'mbo' is a superset of tbbo.",
    )
    args = parser.parse_args(argv)

    # Resolve schemas set
    if args.schema == "both":
        schemas = {"tbbo", "mbo"}
    else:
        schemas = {args.schema}

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df_calls = pd.read_csv(CSV_PATH)
    rows = df_calls.to_dict("records")
    n = len(rows)
    print(f"Loaded {n} Adam /ES calls")
    print(f"Date range: {df_calls['post_ts'].iloc[0]}  →  {df_calls['post_ts'].iloc[-1]}")
    print(f"Output dir: {OUT_DIR}")
    print(f"Hard budget: ${HARD_BUDGET:.2f}  |  Running guard: ${RUNNING_GUARD:.2f}")
    print(f"Schema mode: {args.schema}")
    print()

    estimates = phase1_estimate(rows, schemas)
    projected_total = phase1_report(estimates, schemas)   # exits if > HARD_BUDGET

    if args.dry_run:
        print("--dry-run: stopping here. No data pulled.")
        return 0

    stats = phase2_pull(estimates, schemas, dry_run=False)
    phase3_report(projected_total, stats, schemas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
