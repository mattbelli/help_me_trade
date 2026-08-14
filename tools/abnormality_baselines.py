"""Rebuild the abnormality baseline artifact from the 5-min + daily stores."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from signals.abnormality.baselines import build_baselines


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bars-5min", default=str(REPO_ROOT / "data" / "equity_bars_5min"))
    p.add_argument("--daily", default=str(REPO_ROOT / "data" / "equity_bars_daily"))
    p.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "abnormality"))
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: latest 5-min session)")
    a = p.parse_args()
    as_of = date.fromisoformat(a.as_of) if a.as_of else None
    bl = build_baselines(
        bars_5min_dir=Path(a.bars_5min),
        daily_dir=Path(a.daily),
        out_dir=Path(a.out_dir),
        as_of=as_of,
    )
    n_base = int((bl.base_state > 0).sum())
    print(
        f"baselines {bl.session}: {len(bl.symbols):,} symbols, "
        f"{n_base} with base_state ≠ none"
    )


if __name__ == "__main__":
    main()
