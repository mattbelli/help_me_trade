"""Ratchet on the api/main.py god-module: the numbers may only go DOWN.

Board #77 recorded a "nothing NEW lands in main.py" rule as a note in 2026-07 and the
file still grew 2,872 → 5,347 lines by 2026-08-16. A note is not a rule; a failing
test is. New endpoints go in an ``api/*_routes.py`` router (14+ already exist —
copy one); new background jobs go in a jobs module and get imported into
``_register_background_jobs``. When you extract a family, LOWER these caps in the
same commit so the ratchet keeps biting.
"""

from __future__ import annotations

import re
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "api" / "main.py"

# 2026-08-16 round 1 (6 families out): 41 routes / 4,065 lines.
# 2026-08-16 round 2 (bars, scanner+rvol job, flow, watchlist, alpaca guard out):
# 22 routes / 2,418 lines.
MAX_INLINE_ROUTES = 22
MAX_LINES = 2_440

_ROUTE_RE = re.compile(r"^@app\.(get|post|put|delete|patch|websocket)\(", re.M)


def test_main_py_inline_route_count_only_shrinks() -> None:
    n = len(_ROUTE_RE.findall(MAIN.read_text()))
    assert n <= MAX_INLINE_ROUTES, (
        f"api/main.py has {n} inline routes (cap {MAX_INLINE_ROUTES}). Put the new "
        "endpoint in an api/*_routes.py router and include_router it instead."
    )


def test_main_py_line_count_only_shrinks() -> None:
    n = sum(1 for _ in MAIN.open())
    assert n <= MAX_LINES, (
        f"api/main.py is {n} lines (cap {MAX_LINES}). Extract, don't append — see "
        "board #76 for the next family to pull out."
    )
