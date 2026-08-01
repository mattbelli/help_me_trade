"""Standing open-interest walls — read-only queries over the condensed OI store.

`data/oi_walls.db` is a research artifact built by `tools/oi_condense.py` (OPRA SPX/SPXW
statistics back to 2013 + CME SME) and topped up daily by `tools/sme_oi_refresh.py`. It
is rebuildable, gitignored, and may simply be absent on another machine — every reader
here degrades to a note rather than raising.

What this module does NOT do: claim that a wall pins, magnets, or supports price. No such
edge has been measured (the pre-registered magnet study is unrun), so callers get standing
open interest and its history, described as exactly that.

Structure labelling is GENERIC by design law (board: spx-structural-strikes-study) — a
triple of near-equal-OI legs is reported as collar-SHAPED because that is what our own
rows measure; it is never matched against a hardcoded strike list or fund name.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

OI_WALLS_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "oi_walls.db"
# Trailing span used for days_standing / oi_prior. Calendar days, not sessions: the
# store's `date` is a publish date and the SME product publishes on weekends too.
HISTORY_DAYS = 45
# A triple whose legs sit within this ratio of each other is one order, not three
# coincidences: the observed collar legs match to ~0.02% (24,377/24,377/24,373).
TRIPLE_TOLERANCE = 1.05


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@lru_cache(maxsize=4)
def _sources(db_path: Path) -> tuple[str, ...]:
    """The distinct sources, cached for the process: the column is second in the
    primary key so this costs a 1.3s index skip-scan, and the answer changes only
    when a whole new feed is condensed in (measured 2026-08-01)."""
    with _connect(db_path) as conn:
        return tuple(sorted(r[0] for r in conn.execute("SELECT DISTINCT source FROM oi_daily")))


def store_status(*, db_path: Path | None = None) -> list[dict]:
    """Per-source as-of + staleness. Deliberately only MAX(date) per source (a
    descending index seek, ~0ms): the coverage stats a status block would normally
    carry — first date, dates stored — need GROUP BY source, which is an 8.3s full
    scan of 27M rows and has no business inside an agent's tool call."""
    db_path = db_path or OI_WALLS_DB_PATH
    if not db_path.exists():
        return []
    today = date.today()
    out = []
    with _connect(db_path) as conn:
        for source in _sources(db_path):
            as_of = conn.execute(
                "SELECT MAX(date) FROM oi_daily WHERE source=?", (source,)
            ).fetchone()[0]
            if not as_of:
                continue
            age = (today - date.fromisoformat(as_of)).days
            out.append({
                "source": source,
                "as_of": as_of,
                "as_of_age_days": age,
                "stale": age > 5,
            })
    return out


def _history(
    conn: sqlite3.Connection, source: str, as_of: str, strikes: list[float]
) -> dict[tuple, dict]:
    """days_standing + the previous stored value, for the given strikes only.

    Scoped to a trailing date range so the scan rides the (date, source, ...) primary
    key rather than walking all 27M rows; the strike IN-list keeps the returned set
    small on the OPRA side, where one date carries thousands of strikes.
    """
    if not strikes:
        return {}
    start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
    placeholders = ",".join("?" * len(strikes))
    rows = conn.execute(
        "SELECT root, expiry, cp, strike, date, oi FROM oi_daily"
        f" WHERE date>=? AND date<=? AND source=? AND strike IN ({placeholders})",
        (start, as_of, source, *strikes),
    ).fetchall()
    hist: dict[tuple, dict] = {}
    for r in rows:
        key = (r["root"], r["expiry"], r["cp"], r["strike"])
        h = hist.setdefault(key, {"days_standing": 0, "first_seen": r["date"], "prior": None})
        h["days_standing"] += 1
        h["first_seen"] = min(h["first_seen"], r["date"])
        if r["date"] < as_of and (h["prior"] is None or r["date"] > h["prior"][0]):
            h["prior"] = (r["date"], r["oi"])
    return hist


def _mark_triples(rows: list[dict]) -> None:
    """Flag collar-shaped triples in place: within one expiry, three legs of
    near-equal OI reading put/put/call — the shape of a long put spread financed by a
    short call. A measured description of these rows, not an identification."""
    by_expiry: dict[tuple, list[dict]] = {}
    for r in rows:
        by_expiry.setdefault((r["root"], r["expiry"]), []).append(r)
    for legs in by_expiry.values():
        top = sorted(legs, key=lambda r: r["oi"], reverse=True)[:3]
        if len(top) < 3 or top[2]["oi"] <= 0:
            continue
        if top[0]["oi"] / top[2]["oi"] > TRIPLE_TOLERANCE:
            continue
        if sorted(leg["cp"] for leg in top) != ["C", "P", "P"]:
            continue
        for leg in top:
            leg["structure"] = "collar_shaped_triple"


def standing_walls(
    *,
    root: str | None = None,
    expiry: str | None = None,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[dict]:
    """Largest standing OI strikes on the latest stored date of each source."""
    # Resolved at CALL time, never as a default argument: a module-level default is
    # bound at import, which silently pins every caller (tests included) to the real
    # 3.6 GB store no matter what path they pass or patch.
    db_path = db_path or OI_WALLS_DB_PATH
    if not db_path.exists():
        return []
    out: list[dict] = []
    with _connect(db_path) as conn:
        for status in store_status(db_path=db_path):
            source, as_of = status["source"], status["as_of"]
            # as_of comes from the UNFILTERED max: adding the root/expiry filter to a
            # MAX(date) turns the seek into a full scan of that source's range (1.3s on
            # the OPRA side, measured). A root absent from the latest stored date has no
            # standing walls today, which is the honest answer anyway.
            where, params = ["date=?", "source=?"], [as_of, source]
            if root:
                where.append("root=?")
                params.append(root)
            if expiry:
                where.append("expiry LIKE ?")
                params.append(f"{expiry}%")
            rows = conn.execute(
                "SELECT root, expiry, cp, strike, oi FROM oi_daily"
                f" WHERE {' AND '.join(where)} ORDER BY oi DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            picked = [dict(r) for r in rows]
            hist = _history(conn, source, as_of, sorted({r["strike"] for r in picked}))
            window_start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
            for r in picked:
                h = hist.get((r["root"], r["expiry"], r["cp"], r["strike"]), {})
                prior = h.get("prior")
                seen = h.get("first_seen", as_of)
                r["source"] = source
                r["as_of"] = as_of
                r["as_of_age_days"] = status["as_of_age_days"]
                r["days_standing"] = h.get("days_standing", 1)
                r["history_window"] = f"{window_start}..{as_of}"
                # Only a first sighting strictly inside the window is a real onset date;
                # one sitting on the window edge just means the strike predates the
                # window, and reporting it as "established" would invent a fact.
                r["first_seen"] = seen if seen > window_start else None
                r["oi_prior"] = prior[1] if prior else None
                r["oi_prior_date"] = prior[0] if prior else None
                r["oi_change"] = (r["oi"] - prior[1]) if prior else None
            _mark_triples(picked)
            out.extend(picked)
    out.sort(key=lambda r: r["oi"], reverse=True)
    return out
