"""Replay the v2 decayed-mass scorer over stored fusion investigations.

Read-only against the DB (writes only its markdown report). For every
``fusion_investigations`` row in a date range it recomputes the scorer state
(signals.fusion.scorer.score_decayed) at the row's decision instant, using
only the dots that existed then, plus the symbol-day's PEAK z / maturity and
when they peaked. It emits a per-day table + a cut-line table on the z scale
(candidate T → ranked symbol-days, and DUAL-GATE armable symbol-days: z ≥ T
AND conviction ≥ CONVICTION_BAR) so the founder can pick T — z is THE ranking
statistic per FUSION_SCORING_NORMALIZATION_2026-07-22; maturity cut-lines
(and the 07-21 maturity table) are void.

DB timestamps are UTC; the report displays ET (America/New_York) explicitly.

Usage:
    python tools/fusion_rescore_replay.py [--db PATH] [--start YYYY-MM-DD]
        [--end YYYY-MM-DD] [--out PATH]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.base import DEFAULT_DB_PATH  # noqa: E402
from db.fusion import episode_dots  # noqa: E402
from signals.fusion.scorer import CONVICTION_BAR, _parse_ts, score_decayed  # noqa: E402

_ET = ZoneInfo("America/New_York")
_REPORT_DIR = Path("research/fusion_rescore")
# Candidate z thresholds for the cut-line sweep (z = |net|/√(gross+prior), the
# ranking statistic). Live 07-22 anchors: XLE 3.0 armable, QQQ 6.2 contested —
# the sweep spans noise → strong on that scale. T is an interpretable dial,
# NOT a Gaussian σ (spec: never present z as a p-value).
CUT_LINE_T = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0)


def _et(ts: str) -> str:
    """UTC ISO → 'YYYY-MM-DD HH:MM:SS ET' (explicit zone convert, no +4h trap)."""
    return _parse_ts(ts).astimezone(_ET).strftime("%Y-%m-%d %H:%M:%S ET")


def _dte_map(conn: sqlite3.Connection, event_ids: list[str]) -> dict[str, int]:
    """DTE per options_flow event id — evidence_dots don't persist DTE, so it is
    recovered from the source store by (source_store='options_flow') event id."""
    out: dict[str, int] = {}
    for i in range(0, len(event_ids), 400):
        chunk = event_ids[i : i + 400]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT id, dte FROM options_flow_events WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            if row["dte"] is not None:
                out[str(row["id"])] = int(row["dte"])
    return out


def _load_dots(conn: sqlite3.Connection, episode_id: str, db_path: Path) -> list[dict]:
    """Episode dots with DTE stamped onto options dots (top-level, where the
    scorer looks first)."""
    dots = episode_dots(episode_id, db_path=db_path)
    option_ids = [
        str(dot["source_event_id"])
        for dot in dots
        if dot.get("source_store") == "options_flow"
    ]
    dte_by_id = _dte_map(conn, option_ids) if option_ids else {}
    for dot in dots:
        event_id = str(dot.get("source_event_id"))
        if event_id in dte_by_id:
            dot["dte"] = dte_by_id[event_id]
    return dots


def _peak(dots: list[dict]) -> dict:
    """Day peaks in one pass: max maturity, max thesis z (+ its instant), and
    max ARMABLE z — the peak z among instants where the dual gate's effect-size
    leg held (conviction ≥ CONVICTION_BAR), 0.0 if it never did. Scores only
    rise at a dot arrival (masses decay between arrivals), so candidate
    instants are the dot timestamps themselves."""
    out = {
        "peak_maturity": 0.0,
        "peak_z": 0.0,
        "peak_z_ts": None,
        "peak_armable_z": 0.0,
    }
    instants = sorted({str(dot["ts"]) for dot in dots}, key=lambda ts: _parse_ts(ts))
    for now_ts in instants:
        cutoff = _parse_ts(now_ts)
        visible = [dot for dot in dots if _parse_ts(dot["ts"]) <= cutoff]
        state = score_decayed(visible, now_ts)
        thesis = state["thesis"]
        out["peak_maturity"] = max(out["peak_maturity"], state["maturity"])
        if thesis["z"] > out["peak_z"]:
            out["peak_z"] = thesis["z"]
            out["peak_z_ts"] = now_ts
        if thesis["conviction"] >= CONVICTION_BAR:
            out["peak_armable_z"] = max(out["peak_armable_z"], thesis["z"])
    return out


def rescore_range(
    db_path: Path = DEFAULT_DB_PATH,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Replay every investigation row in [start, end]; return a report dict."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        args: list[str] = []
        if start:
            clauses.append("market_day >= ?")
            args.append(start)
        if end:
            clauses.append("market_day <= ?")
            args.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT episode_id, market_day, symbol, decided_at, decision, score, threshold
            FROM fusion_investigations {where}
            ORDER BY market_day, decided_at
            """,
            args,
        ).fetchall()

        records: list[dict] = []
        for row in rows:
            dots = _load_dots(conn, str(row["episode_id"]), db_path)
            decided = _parse_ts(str(row["decided_at"]))
            at_decision = [dot for dot in dots if _parse_ts(dot["ts"]) <= decided]
            state = score_decayed(at_decision, str(row["decided_at"]))
            peaks = _peak(dots)
            records.append(
                {
                    "market_day": str(row["market_day"]),
                    "symbol": str(row["symbol"]),
                    "decided_at": str(row["decided_at"]),
                    "decision": str(row["decision"]),
                    "old_score": row["score"],
                    "maturity": state["maturity"],
                    "z": state["thesis"]["z"],
                    "thesis_bull": state["thesis"]["bull_mass"],
                    "thesis_bear": state["thesis"]["bear_mass"],
                    "conviction": state["thesis"]["conviction"],
                    "contested": state["thesis"]["contested"],
                    "peak_maturity": peaks["peak_maturity"],
                    "peak_z": peaks["peak_z"],
                    "peak_z_ts": peaks["peak_z_ts"],
                    "peak_armable_z": peaks["peak_armable_z"],
                }
            )
        return {"records": records}
    finally:
        conn.close()


def _render(report: dict) -> str:
    records = report["records"]
    by_day: dict[str, list[dict]] = {}
    for rec in records:
        by_day.setdefault(rec["market_day"], []).append(rec)
    days = sorted(by_day)

    lines: list[str] = []
    lines.append("# Fusion v2 rescore replay — decayed masses + conviction")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(_ET).strftime('%Y-%m-%d %H:%M:%S ET')} · "
        f"{len(records)} investigation rows across {len(days)} day(s)."
    )
    lines.append("")
    lines.append(
        "**z** = |bull − bear| / √(gross + prior) — THE ranking statistic "
        "(FUSION_SCORING_NORMALIZATION_2026-07-22). Armable = the DUAL gate: "
        "z ≥ T AND conviction ≥ "
        f"{CONVICTION_BAR:.2f} (z alone re-admits megacap dominance). Maturity "
        "kept for side-by-side only; old score = the undecayed all-day sum "
        "stored at decision. Peak = the day's max and the ET instant z peaked."
    )
    lines.append("")

    for day in days:
        rows = by_day[day]
        lines.append(f"## {day}")
        lines.append("")
        lines.append(
            "| symbol | decision time (ET) | old score | z | maturity | "
            "thesis bull | thesis bear | conviction | contested | peak z | "
            "peak armable z | peak z (ET) | decision |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for rec in sorted(rows, key=lambda r: r["z"], reverse=True):
            peak_et = _et(rec["peak_z_ts"]) if rec["peak_z_ts"] else "—"
            old = "—" if rec["old_score"] is None else f"{rec['old_score']:.2f}"
            lines.append(
                f"| {rec['symbol']} | {_et(rec['decided_at'])} | {old} | "
                f"{rec['z']:.3f} | {rec['maturity']:.3f} | {rec['thesis_bull']:.3f} | "
                f"{rec['thesis_bear']:.3f} | {rec['conviction']:.3f} | "
                f"{'yes' if rec['contested'] else 'no'} | "
                f"{rec['peak_z']:.3f} | {rec['peak_armable_z']:.3f} | "
                f"{peak_et} | {rec['decision']} |"
            )
        lines.append("")

    # Cut-line on the z scale: per candidate T, how many symbol-days RANK at/
    # above it (peak z) vs how many could ARM under the dual gate (peak armable
    # z — z at an instant where conviction also held the bar).
    lines.append("## Cut-line — candidate z threshold T (dual gate)")
    lines.append("")
    lines.append(
        "'ranked' = peak z ≥ T (visible at the top of the board); 'armable' = "
        "peak ARMABLE z ≥ T (dual gate: conviction ≥ "
        f"{CONVICTION_BAR:.2f} at that instant). The gap between the columns is "
        "the contested set the gate exists to hold back. Counts are symbol-days."
    )
    lines.append("")
    header = (
        "| T | "
        + " | ".join(f"{d} (ranked / armable)" for d in days)
        + " | total (ranked / armable) |"
    )
    lines.append(header)
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
    parser.add_argument("--start", default=None, help="market_day lower bound (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="market_day upper bound (YYYY-MM-DD)")
    parser.add_argument("--out", default=None, type=Path, help="report path override")
    args = parser.parse_args()

    report = rescore_range(args.db, args.start, args.end)
    records = report["records"]
    if not records:
        print("no fusion_investigations rows in range — nothing to rescore")
        return

    days = sorted({rec["market_day"] for rec in records})
    out = args.out or (_REPORT_DIR / f"rescore_{days[0]}_{days[-1]}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render(report))
    print(f"wrote {out} ({len(records)} rows, {days[0]}..{days[-1]})")


if __name__ == "__main__":
    main()
