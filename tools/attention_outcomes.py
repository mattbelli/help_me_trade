"""Grade every pool name-day into attention_outcomes (design §4.2 nightly)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from signals.abnormality.baselines import load_baselines_before
from signals.attention_outcomes import sweep_attention_outcomes
from trading.market_calendar import last_closed_session


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session", default=None)
    p.add_argument("--bars-5min", default=str(REPO_ROOT / "data" / "equity_bars_5min"))
    p.add_argument("--baselines-dir", default=str(REPO_ROOT / "data" / "abnormality"))
    p.add_argument("--db", default=None)
    a = p.parse_args()
    session = date.fromisoformat(a.session) if a.session else last_closed_session()
    # Artifact strictly before the graded session — the forward norm must not
    # contain the session it normalizes (registered no-same-day-leakage rule).
    bl = load_baselines_before(session, Path(a.baselines_dir))
    if bl is None:
        raise SystemExit(f"no baselines artifact built before {session}")
    n = sweep_attention_outcomes(
        session,
        bars_5min_dir=Path(a.bars_5min),
        baselines=bl,
        db_path=Path(a.db) if a.db else None,
    )
    print(f"{session}: graded {n} pool name-days into attention_outcomes")


if __name__ == "__main__":
    main()
