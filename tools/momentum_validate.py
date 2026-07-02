"""Gate 1 — momentum port correctness against the PC's golden target sets.

Runs the production ranking (research.momentum_reference.compute_target_set) on the
survivorship-free Norgate VALIDATION SLICE for the 4 golden dates and diffs against
research/fixtures/momentum_targets.json. A clean diff = the port is algorithmically correct
(isolated from data differences) — the gate to wire bulk execution (after the sanity gate +
adversarial review).

Run on the SLICE, never on Schwab prod bars (Schwab won't match — different prices/universe/
artifacts; that's expected). The slice is licensed Norgate data — scp it in, do NOT commit it.

Usage:
  scp the slice to data/research/bars_validation_slice.db (gitignored), then:
  .venv/bin/python tools/momentum_validate.py
  .venv/bin/python tools/momentum_validate.py /path/to/bars_validation_slice.db
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.momentum_reference import (  # noqa: E402
    compute_target_set,
    load_bars_for_symbols,
    load_sector_map,
)

DEFAULT_SLICE = Path("data/research/bars_validation_slice.db")
GOLDEN = Path("research/fixtures/momentum_targets.json")
REL_TOL = 1e-6  # 6 significant figures, per the fixtures contract


def main() -> int:
    slice_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SLICE
    if not slice_path.exists():
        print(f"❌ validation slice not found at {slice_path}")
        print("   scp bars_validation_slice.db there first (out-of-band — NOT git).")
        return 2

    golden = json.loads(GOLDEN.read_text())
    sector_map = load_sector_map()
    bars = load_bars_for_symbols(list(sector_map.keys()), bars_db=slice_path)
    print(f"loaded bars for {len(bars)} / {len(sector_map)} sector-mapped symbols from {slice_path.name}\n")

    all_pass = True
    for ts in golden["target_sets"]:
        as_of = ts["as_of_date"]
        gold_syms = sorted(ts["target_symbols"])
        gold_scores = ts.get("residual_momentum", {})

        result = compute_target_set(bars, sector_map, as_of)
        got_syms = sorted(d["symbol"] for d in result)
        got_scores = {d["symbol"]: d["residual_momentum"] for d in result}

        syms_match = got_syms == gold_syms
        score_ok = True
        max_diff = 0.0
        for s in set(gold_syms) & set(got_syms):
            a, b = got_scores[s], gold_scores.get(s)
            if b is None:
                continue
            max_diff = max(max_diff, abs(a - b))
            if not math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-9):
                score_ok = False

        ok = syms_match and score_ok
        all_pass = all_pass and ok
        print(
            f"  {as_of}: symbols {'MATCH' if syms_match else 'DIFFER'} "
            f"({len(got_syms)} vs {len(gold_syms)}) | scores "
            f"{'ok' if score_ok else 'OFF'} (max|Δ|={max_diff:.2e}) -> "
            f"{'PASS ✅' if ok else 'FAIL ❌'}"
        )
        if not syms_match:
            missing = sorted(set(gold_syms) - set(got_syms))
            extra = sorted(set(got_syms) - set(gold_syms))
            if missing:
                print(f"      in golden, not ours: {missing[:12]}")
            if extra:
                print(f"      ours, not golden:    {extra[:12]}")

    print()
    print("GATE 1:", "✅ PASS — port is algorithmically correct" if all_pass else "❌ FAIL — port diverges")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
