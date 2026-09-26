"""Print the brief A/B scorecard: specialist x arm, over nightly and weekly runs.

Grader win rate and mean scores, numbers not found in our data per memo, forward
calls resolved against our daily bars / data/macro.db with a Wilson range, and
mean wall time. Calls resolve the morning after their target session.

Usage:
    python tools/brief_scorecard.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.brief.scorecard import collect, render  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    rows = collect()
    print(render(rows) if rows else
          "no scored runs yet (data/briefs/<date>/ and <date>-weekly/ are empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
