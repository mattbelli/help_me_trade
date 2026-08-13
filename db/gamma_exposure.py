"""Gamma-exposure snapshot store — the history a percentile needs.

Its own database (``data/gamma_exposure.db``), deliberately not ``macro.db``:
that store holds vendor-printed economic series, and these rows are OUR model
output off an option board. Filing model output beside measured data is how a
provenance smear starts — a later consumer reading "macro" would have no way to
tell the two apart.

Why a store at all: every headline number here except the dollar magnitudes is
interpretable on its own (a flip level, a distance to it, a sign). The magnitudes
are not — "$65.8B of net gamma" means nothing without knowing whether that is a
big number for this board, and that goes double for net vanna and net charm,
which have no folk reference point at all. That takes history, and there is none
to backfill: the OI store goes to 2013 but per-strike index IV does not exist
anywhere we hold, so a historical greek would have to be modelled from a flat VIX
and would be a statement about the model, not the market. So history accrues
forward from the first snapshot, and until it is deep enough the percentile is
reported as absent rather than computed off a handful of days.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import ROOT
from db.base import connect as _connect

GEX_DB_PATH = ROOT / "data" / "gamma_exposure.db"

# Below this a percentile is noise dressed as a statistic — a rank against twelve
# observations moves 8 points per new row. ~60 sessions is a quarter of trading
# days, the shortest window worth naming.
MIN_OBSERVATIONS = 60

_FIELDS = (
    "session", "oi_as_of", "spot", "net_b", "call_b", "put_b",
    "gross_b", "flip", "spot_above_flip_pct", "oi_covered_pct",
    "net_vanna_b", "net_charm_b",
)

# Added after the first snapshots were already stored. Nullable on purpose: the
# rows taken before vanna and charm existed genuinely have no value for them, and
# a backfilled zero would be indistinguishable from a board that netted to zero.
_ADDED_COLUMNS = {"net_vanna_b": "REAL", "net_charm_b": "REAL"}


def init_gex_table(db_path: Path = GEX_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gex_snapshots (
                as_of TEXT PRIMARY KEY,
                session TEXT NOT NULL,
                oi_as_of TEXT NOT NULL,
                spot REAL NOT NULL,
                net_b REAL NOT NULL,
                call_b REAL NOT NULL,
                put_b REAL NOT NULL,
                gross_b REAL NOT NULL,
                flip REAL,
                spot_above_flip_pct REAL,
                oi_covered_pct REAL,
                net_vanna_b REAL,
                net_charm_b REAL
            )
            """
        )
        existing = {r[1] for r in conn.execute("PRAGMA table_info(gex_snapshots)")}
        for name, decl in _ADDED_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE gex_snapshots ADD COLUMN {name} {decl}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gex_session ON gex_snapshots (session)"
        )
        conn.commit()


def record(summary: dict[str, Any], *, db_path: Path = GEX_DB_PATH) -> str | None:
    """Persist one summarised board. Returns the as_of stored, or None if unusable.

    Keyed on the QUOTE timestamp, not the session: a snapshot taken twice from the
    same frozen close is the same observation and must not enter the history twice,
    while two genuine intraday reads are two observations.
    """
    as_of = summary.get("quote_as_of")
    horizons = summary.get("by_horizon") or []
    total = next((h for h in horizons if h.get("dte_max") is None), None)
    if not as_of or total is None:
        return None
    init_gex_table(db_path)
    row = (
        as_of,
        str(as_of)[:10],
        summary["oi_as_of"],
        summary["spot"],
        summary["net_gamma_usd_per_1pct_b"],
        summary["call_gamma_usd_per_1pct_b"],
        summary["put_gamma_usd_per_1pct_b"],
        total["gross_usd_per_1pct_b"],
        summary.get("gamma_flip_spot"),
        summary.get("spot_above_flip_pct"),
        (summary.get("coverage") or {}).get("open_interest_covered_pct"),
        summary.get("net_vanna_usd_per_volpt_b"),
        summary.get("net_charm_usd_per_day_b"),
    )
    with _connect(db_path) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO gex_snapshots (as_of, {', '.join(_FIELDS)})"
            f" VALUES ({', '.join('?' * (len(_FIELDS) + 1))})",
            row,
        )
        conn.commit()
    return str(as_of)


def history(*, limit: int = 120, db_path: Path = GEX_DB_PATH) -> list[dict[str, Any]]:
    """Stored snapshots, newest first — the cap drops the oldest, which is right."""
    if not db_path.exists():
        return []
    init_gex_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT as_of, {', '.join(_FIELDS)} FROM gex_snapshots"
            " ORDER BY as_of DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
    return [dict(zip(("as_of", *_FIELDS), r, strict=True)) for r in rows]


def percentile(
    field: str, value: float, *, db_path: Path = GEX_DB_PATH
) -> dict[str, Any]:
    """Rank ``value`` against every stored observation of ``field``.

    Always returns the observation count, so a missing percentile says WHY it is
    missing rather than just being absent.
    """
    if field not in _FIELDS:
        raise ValueError(f"unknown field {field!r}")
    values: list[float] = []
    if db_path.exists():
        init_gex_table(db_path)
        with _connect(db_path) as conn:
            values = [
                r[0]
                for r in conn.execute(
                    f"SELECT {field} FROM gex_snapshots WHERE {field} IS NOT NULL"
                )
            ]
    out: dict[str, Any] = {"observations": len(values)}
    if len(values) < MIN_OBSERVATIONS:
        out["percentile"] = None
        out["note"] = (
            f"needs {MIN_OBSERVATIONS} stored observations to rank; history accrues "
            "forward from the first snapshot and cannot be backfilled (no per-strike "
            "index IV exists before it)"
        )
        return out
    at_or_below = sum(1 for v in values if v <= value)
    out["percentile"] = round(100 * at_or_below / len(values), 1)
    out["window"] = f"{len(values)} stored snapshots"
    return out


def percentiles(
    values: dict[str, float], *, db_path: Path = GEX_DB_PATH
) -> dict[str, Any]:
    """Rank several fields at once, with the shared caveat said exactly once.

    Three axes each carrying their own copy of the same 200-character "not enough
    history yet" note is three times the tokens for one fact — and it reads like
    three separate problems rather than one store that is young.
    """
    ranked = {field: percentile(field, value, db_path=db_path)
              for field, value in values.items()}
    out: dict[str, Any] = {
        "observations": max((r["observations"] for r in ranked.values()), default=0),
        "percentiles": {field: r["percentile"] for field, r in ranked.items()},
    }
    notes = {r["note"] for r in ranked.values() if r.get("note")}
    if notes:
        out["note"] = sorted(notes)[0]
    return out
