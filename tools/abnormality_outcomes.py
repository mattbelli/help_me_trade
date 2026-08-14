"""Grade yesterday's abnormality fires from the 5-min store into abnormality_outcomes."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from db.abnormality import insert_outcomes, ungraded_events
from signals.abnormality.baselines import load_baselines
from signals.abnormality.outcomes import sweep_session
from trading.market_calendar import last_closed_session


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session", default=None)
    p.add_argument("--bars-5min", default=str(REPO_ROOT / "data" / "equity_bars_5min"))
    p.add_argument("--baselines-dir", default=str(REPO_ROOT / "data" / "abnormality"))
    p.add_argument("--db", default=None)
    a = p.parse_args()
    session = date.fromisoformat(a.session) if a.session else last_closed_session()
    db = Path(a.db) if a.db else None
    bl = load_baselines(Path(a.baselines_dir))
    if bl is None:
        raise SystemExit("no baselines artifact")
    events = ungraded_events(session.isoformat(), db_path=db)
    if not events:
        print(f"{session}: 0 ungraded events")
        return
    rows = sweep_session(session, events, Path(a.bars_5min), bl)
    n = insert_outcomes(rows, db_path=db)
    print(f"{session}: graded {n}/{len(events)} events")


if __name__ == "__main__":
    main()
