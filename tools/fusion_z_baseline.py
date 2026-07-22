"""Backfill + rebuild the ``fusion_z_baseline`` table (Stage-2 baseline lens).

Computes per symbol × market-day thesis-z summaries for the per-symbol
baseline percentile (FUSION_SCORING_NORMALIZATION_2026-07-22, Stage 2), from
the deepest VALID history available:

- Days that have ``evidence_dots`` replay the dots (live parity — exactly what
  the radar scores), with DTE recovered from ``options_flow_events`` like the
  radar/replay do.
- Earlier days replay the RAW native stores (``options_flow_events`` +
  ``equity_tape_events``) through the SAME intake shaping
  (``signals.fusion.intake._event_shape``) and the SAME scorer — one code
  path, no live-vs-backfill methodology drift. Fidelity gap, labeled in the
  ``source`` column: raw replay has no storm-shed or unstampable exclusions,
  and covers only the flow + tape components (social/earnings/regime dots are
  not reconstructable) — never compare a raw_events row to a full-stack live z
  without that flag.

FROZEN summary rule (consumers must not improvise a different one):
- Instants = a 5-minute ET grid, 09:35..16:00, per day. Scores only rise at a
  dot arrival and thesis decay over 5 minutes is ~0.35%, so grid peaks
  under-read true arrival-instant peaks by <0.5% — uniformly across symbols,
  which is all a percentile needs.
- ``peak_z``   = max thesis z over the grid (the intraday Stage-2 column
  compares today's RUNNING-max z to trailing daily peaks — peak-vs-peak).
- ``close_z``  = thesis z at the 16:00 ET grid point (EOD/grading uses).
- ``peak_armable_z`` = max thesis z over grid instants where conviction also
  held CONVICTION_BAR (the dual gate's effect-size leg) — the T-table fuel.
- Visibility at an instant = all of the symbol's dots from the trailing
  LOOKBACK_SESSIONS available baseline days (mirrors the radar's 5-session
  window), each day taken from its own single source (never both — no double
  counting).

Stationarity: rows are NEVER computed before ``BASELINE_FLOOR_DAY``. Stage-2
percentiles stay ADVISORY until a symbol has ``MIN_BASELINE_DAYS`` rows — a
percentile over 4 values is a rank, not a distribution (consumer-enforced;
recorded here so the number has one home).

Writes: ``fusion_z_baseline`` rows (INSERT OR REPLACE — idempotent, re-run at
will) + a markdown report with the z cut-line table. The write path also runs
``init_fusion_tables`` (idempotent CREATEs + component-group upserts — the
standard init, no data mutation); computation itself is read-only.

Usage:
    uv run python tools/fusion_z_baseline.py [--db PATH] [--start YYYY-MM-DD]
        [--end YYYY-MM-DD] [--out PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.base import DEFAULT_DB_PATH, now_iso  # noqa: E402
from db.base import connect as _connect  # noqa: E402
from db.fusion import init_fusion_tables  # noqa: E402
from signals.fusion.intake import _event_shape, _market_day  # noqa: E402
from signals.fusion.scorer import CONVICTION_BAR, _parse_ts, score_decayed  # noqa: E402

_ET = ZoneInfo("America/New_York")
_REPORT_DIR = Path("research/fusion_rescore")

# HARD stationarity floor — a LATCH, not a suggestion. Before this date our
# own collection was non-stationary: the flow sub cap was alphabetically
# clipped until #73 (SPY/TSLA literally unwatched) and OI-seeded pre-bell subs
# landed ~07-15; the full equity-tape lane went live 07-16. Pre-floor
# "normals" would poison exactly the names Stage 2 exists to normalize.
# ⚠ Bump this date on ANY future collection change (new sub universe, new
# gating) — same disease, smaller blast radius. Shaping-code changes do NOT
# move the floor: raw days are replayed through current shaping every rebuild.
BASELINE_FLOOR_DAY = "2026-07-16"
LOOKBACK_SESSIONS = 5  # mirrors the radar's default trailing window
MIN_BASELINE_DAYS = 10  # Stage-2 percentile advisory floor (consumer-enforced)
GRID_MINUTES = 5
CUT_LINE_T = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0)


def _utc_bounds(day: str) -> tuple[str, str]:
    """[start, end) UTC ISO bounds of one ET calendar day."""
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, tzinfo=_ET)
    end = start + timedelta(days=1)
    return start.astimezone(ZoneInfo("UTC")).isoformat(), end.astimezone(
        ZoneInfo("UTC")
    ).isoformat()


def _grid_instants(day: str) -> list[datetime]:
    """The frozen 5-minute ET evaluation grid: 09:35, 09:40, .. 16:00."""
    d = date.fromisoformat(day)
    open_dt = datetime(d.year, d.month, d.day, 9, 30, tzinfo=_ET)
    close_dt = datetime(d.year, d.month, d.day, 16, 0, tzinfo=_ET)
    out = []
    t = open_dt + timedelta(minutes=GRID_MINUTES)
    while t <= close_dt:
        out.append(t)
        t += timedelta(minutes=GRID_MINUTES)
    return out


def _load_day_dots(conn: sqlite3.Connection, day: str) -> dict[str, list[dict]]:
    """One ET day's evidence_dots by symbol, DTE recovered for option dots.

    detail_json is parsed into ``detail`` exactly like the radar does — the
    scorer reads the equity per-event ``floor_usd`` base (and a fallback DTE)
    from there; skipping the parse silently over-scores liquid equity names
    against the fallback base and breaks live parity."""
    lo, hi = _utc_bounds(day)
    rows = []
    for row in conn.execute(
        "SELECT * FROM evidence_dots WHERE ts >= ? AND ts < ? ORDER BY ts, id",
        (lo, hi),
    ).fetchall():
        dot = dict(row)
        try:
            dot["detail"] = json.loads(dot.pop("detail_json"))
        except (TypeError, json.JSONDecodeError):
            dot["detail"] = {}
        rows.append(dot)
    option_ids = [
        str(row["source_event_id"]) for row in rows if row["source_store"] == "options_flow"
    ]
    dte_by_id: dict[str, int] = {}
    for start in range(0, len(option_ids), 400):
        chunk = option_ids[start : start + 400]
        ph = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT id, dte FROM options_flow_events WHERE id IN ({ph})", chunk
        ).fetchall():
            if row["dte"] is not None:
                dte_by_id[str(row["id"])] = int(row["dte"])
    by_symbol: dict[str, list[dict]] = {}
    for dot in rows:
        event_id = str(dot.get("source_event_id"))
        if event_id in dte_by_id:
            dot["dte"] = dte_by_id[event_id]
        by_symbol.setdefault(str(dot["symbol"]), []).append(dot)
    return by_symbol


def _load_day_raw(conn: sqlite3.Connection, day: str) -> dict[str, list[dict]]:
    """One ET day's raw store events reshaped into pseudo-dots by symbol."""
    lo, hi = _utc_bounds(day)
    by_symbol: dict[str, list[dict]] = {}
    for store, table in (
        ("options_flow", "options_flow_events"),
        ("equity_tape", "equity_tape_events"),
    ):
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE ts >= ? AND ts < ? ORDER BY ts, id", (lo, hi)
        ).fetchall():
            event = dict(row)
            shape = _event_shape(store, event)
            if shape is None or not shape["symbol"] or shape["direction"] == "none":
                continue
            if shape["raw_value"] is None:
                continue
            by_symbol.setdefault(shape["symbol"], []).append(
                {
                    "id": f"raw-{store}-{event['id']}",
                    "symbol": shape["symbol"],
                    "ts": str(event["ts"]),
                    "component": shape["component"],
                    "actor_group": shape["actor_group"],
                    "direction": shape["direction"],
                    "raw_value": str(shape["raw_value"]),
                    "stamped": 1,
                    "detail": shape["detail"],
                }
            )
    return by_symbol


def _candidate_days(conn: sqlite3.Connection, start: str, end: str) -> list[tuple[str, str]]:
    """(day, source) for every ET day in [start, end] that has data — 'dots'
    when the day has any evidence_dots, else 'raw_events'. Days with neither
    (weekends/holidays/collector gaps) are skipped."""
    days: list[tuple[str, str]] = []
    d = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while d <= last:
        day = d.isoformat()
        d += timedelta(days=1)
        lo, hi = _utc_bounds(day)
        has_dots = conn.execute(
            "SELECT 1 FROM evidence_dots WHERE ts >= ? AND ts < ? LIMIT 1", (lo, hi)
        ).fetchone()
        if has_dots:
            days.append((day, "dots"))
            continue
        has_raw = conn.execute(
            "SELECT 1 FROM options_flow_events WHERE ts >= ? AND ts < ? LIMIT 1", (lo, hi)
        ).fetchone() or conn.execute(
            "SELECT 1 FROM equity_tape_events WHERE ts >= ? AND ts < ? LIMIT 1", (lo, hi)
        ).fetchone()
        if has_raw:
            days.append((day, "raw_events"))
    return days


def rebuild(
    db_path: Path = DEFAULT_DB_PATH,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Recompute baseline rows for [start, end] (floor-clamped). Returns the
    report dict; writes rows unless the caller strips them (see --dry-run)."""
    start = max(start or BASELINE_FLOOR_DAY, BASELINE_FLOOR_DAY)
    end = end or _market_day(now_iso())
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        day_sources = _candidate_days(conn, start, end)
        # Per-day single-source loads (never both — no double counting).
        dots_by_day: dict[str, dict[str, list[dict]]] = {}
        for day, source in day_sources:
            loader = _load_day_dots if source == "dots" else _load_day_raw
            dots_by_day[day] = loader(conn, day)
    finally:
        conn.close()

    computed_at = now_iso()
    rows: list[dict] = []
    ordered_days = [day for day, _ in day_sources]
    for idx, (day, source) in enumerate(day_sources):
        window_days = ordered_days[max(0, idx - (LOOKBACK_SESSIONS - 1)) : idx + 1]
        for symbol, day_dots in dots_by_day[day].items():
            # Timeline sorted on PARSED instants (never lexical — 'Z' vs
            # '+00:00' suffixes would shuffle a string sort), walked once with
            # an advancing pointer per grid instant.
            timeline = sorted(
                (
                    dot
                    for wday in window_days
                    for dot in dots_by_day[wday].get(symbol, [])
                ),
                key=lambda dot: _parse_ts(dot["ts"]),
            )
            parsed = [_parse_ts(dot["ts"]) for dot in timeline]
            peak_z = 0.0
            peak_z_ts: str | None = None
            peak_armable_z = 0.0
            close_z = 0.0
            evaluated = False
            ptr = 0
            for instant in _grid_instants(day):
                while ptr < len(parsed) and parsed[ptr] <= instant:
                    ptr += 1
                if ptr == 0:
                    continue
                evaluated = True
                # Stored UTC like every other timestamp in the DB (grid
                # instants are ET-aware internally).
                cutoff = instant.astimezone(timezone.utc).isoformat()
                thesis = score_decayed(timeline[:ptr], cutoff)["thesis"]
                if thesis["z"] > peak_z:
                    peak_z = thesis["z"]
                    peak_z_ts = cutoff
                if thesis["conviction"] >= CONVICTION_BAR:
                    peak_armable_z = max(peak_armable_z, thesis["z"])
                close_z = thesis["z"]  # last grid instant standing = 16:00 ET
            if not evaluated:
                # Nothing was ever visible on the RTH grid (e.g. the symbol's
                # only dots landed after 16:00 ET with an empty trailing
                # window). A z=0 row here would be a FALSE "contested all day"
                # sample in the Stage-2 distribution — no row beats a wrong row.
                continue
            # Directional actor dots only — the same population both sources
            # feed into masses — so the count is comparable across the
            # raw/dots boundary (dots-days also mint structure/context dots).
            actor_count = sum(
                1
                for dot in day_dots
                if dot.get("actor_group") in ("options_actor", "equity_actor")
                and dot.get("direction") in ("bull", "bear")
                and bool(dot.get("stamped", 1))
            )
            rows.append(
                {
                    "symbol": symbol,
                    "market_day": day,
                    "peak_z": round(peak_z, 4),
                    "peak_z_ts": peak_z_ts,
                    "peak_armable_z": round(peak_armable_z, 4),
                    "close_z": round(close_z, 4),
                    "dot_count": actor_count,
                    "source": source,
                    "computed_at": computed_at,
                }
            )
    return {"rows": rows, "days": day_sources, "start": start, "end": end}


def write_rows(report: dict, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Replace the rebuilt window's rows wholesale: DELETE [start, end] then
    insert. Plain INSERT OR REPLACE would strand stale rows for any
    (symbol, day) the rebuild no longer emits — e.g. rows a fixed computation
    now (correctly) skips."""
    rows = report["rows"]
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM fusion_z_baseline WHERE market_day >= ? AND market_day <= ?",
            (report["start"], report["end"]),
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO fusion_z_baseline(
                symbol, market_day, peak_z, peak_z_ts, peak_armable_z,
                close_z, dot_count, source, computed_at
            ) VALUES (
                :symbol, :market_day, :peak_z, :peak_z_ts, :peak_armable_z,
                :close_z, :dot_count, :source, :computed_at
            )
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def _render(report: dict) -> str:
    rows = report["rows"]
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        by_day.setdefault(row["market_day"], []).append(row)
    days = sorted(by_day)
    source_by_day = dict(report["days"])

    lines: list[str] = []
    lines.append("# fusion_z_baseline rebuild — per-symbol-day thesis z")
    lines.append("")
    lines.append(
        f"Window {report['start']}..{report['end']} · {len(rows)} symbol-days "
        f"across {len(days)} day(s) · floor {BASELINE_FLOOR_DAY} (latch — see "
        "tool docstring) · frozen rule: 5-min ET grid, peak-vs-peak intraday / "
        "close_z EOD. Stage-2 percentiles stay ADVISORY below "
        f"{MIN_BASELINE_DAYS} rows per symbol."
    )
    lines.append("")

    for day in days:
        day_rows = sorted(by_day[day], key=lambda r: r["peak_z"], reverse=True)
        lines.append(
            f"## {day} — source `{source_by_day.get(day, '?')}`, "
            f"{len(day_rows)} symbols"
        )
        lines.append("")
        lines.append("| symbol | peak z | peak armable z | close z | dots |")
        lines.append("|---|---|---|---|---|")
        for row in day_rows[:15]:
            lines.append(
                f"| {row['symbol']} | {row['peak_z']:.3f} | "
                f"{row['peak_armable_z']:.3f} | {row['close_z']:.3f} | "
                f"{row['dot_count']} |"
            )
        if len(day_rows) > 15:
            lines.append(f"| … | +{len(day_rows) - 15} more | | | |")
        lines.append("")

    lines.append("## Cut-line — candidate z threshold T (dual gate)")
    lines.append("")
    lines.append(
        "'ranked' = peak z ≥ T; 'armable' = peak ARMABLE z ≥ T (conviction ≥ "
        f"{CONVICTION_BAR:.2f} held at that instant). Counts are symbol-days."
    )
    lines.append("")
    lines.append(
        "| T | "
        + " | ".join(f"{d} (ranked / armable)" for d in days)
        + " | total (ranked / armable) |"
    )
    lines.append("|" + "---|" * (len(days) + 2))
    for threshold in CUT_LINE_T:
        cells = []
        tot_ranked = tot_armable = 0
        for day in days:
            ranked = sum(1 for r in by_day[day] if r["peak_z"] >= threshold)
            armable = sum(1 for r in by_day[day] if r["peak_armable_z"] >= threshold)
            tot_ranked += ranked
            tot_armable += armable
            cells.append(f"{ranked} / {armable}")
        lines.append(
            f"| {threshold:.2f} | "
            + " | ".join(cells)
            + f" | {tot_ranked} / {tot_armable} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), type=Path)
    parser.add_argument("--start", default=None, help="ET day lower bound (floor-clamped)")
    parser.add_argument("--end", default=None, help="ET day upper bound (default today ET)")
    parser.add_argument("--out", default=None, type=Path, help="report path override")
    parser.add_argument(
        "--dry-run", action="store_true", help="compute + report, write no table rows"
    )
    args = parser.parse_args()

    report = rebuild(args.db, args.start, args.end)
    if not report["rows"]:
        print("no symbol-days in range — nothing to build")
        return
    if not args.dry_run:
        written = write_rows(report, args.db)
        print(f"wrote {written} fusion_z_baseline rows")
    out = args.out or (
        _REPORT_DIR / f"z_baseline_{report['start']}_{report['end']}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render(report))
    print(f"wrote {out} ({len(report['rows'])} symbol-days, {len(report['days'])} days)")


if __name__ == "__main__":
    main()
