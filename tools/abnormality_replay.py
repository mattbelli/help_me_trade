"""Replay the abnormality fire rule over the 5-min store — fire-rate tables."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from signals.abnormality.replay import replay_candidates


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bars-5min", default=str(REPO_ROOT / "data" / "equity_bars_5min"))
    p.add_argument("--baselines-dir", default=str(REPO_ROOT / "data" / "abnormality"))
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--out", default=None, help="write JSON tables here")
    a = p.parse_args()
    tables = replay_candidates(
        Path(a.bars_5min),
        baselines_dir=Path(a.baselines_dir),
        start=date.fromisoformat(a.start) if a.start else None,
        end=date.fromisoformat(a.end) if a.end else None,
    )
    for t in tables:
        thr = t["thresholds"]
        print(
            f"p{thr['range_p']:g} single-component occ>={thr['min_occupancy']}: "
            f"{t['fires']} first fires / {t['sessions']} sess "
            f"({t['fires_per_day']}/day), {t['paired']} paired "
            f"({t['paired_per_week']}/week)"
        )
        for ex in (t.get("examples") or [])[:8]:
            print(
                f"  {ex['symbol']} b{ex['bucket']} {ex['kind']} "
                f"range={ex['range_ratio']:.1f}× vol={ex['vol_ratio']:.1f}×"
            )
    if a.out:
        Path(a.out).write_text(json.dumps(tables, indent=1, default=str))


if __name__ == "__main__":
    main()
