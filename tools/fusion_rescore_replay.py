"""Replay the v2 decayed-mass scorer over stored fusion investigations.

Read-only against the DB (writes only its markdown report). For every
``fusion_investigations`` row in a date range it recomputes the NEW maturity
score (signals.fusion.scorer.score_decayed) at the row's decision instant, using
only the dots that existed then, plus the symbol-day's PEAK maturity and when it
peaked. It emits a per-day table + a cut-line table (candidate T → how many
symbol-days qualify) so the founder can pick a fresh maturity threshold T — the
07-21 cut-line is void under decay+conviction scoring (redesign §4/§6 step 2).

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
from signals.fusion.scorer import _parse_ts, score_decayed  # noqa: E402

_ET = ZoneInfo("America/New_York")
_REPORT_DIR = Path("research/fusion_rescore")
# Candidate maturity thresholds for the cut-line sweep. Maturity here is
# thesis_gross × thesis_conviction (= |net decayed thesis mass|); a lone
# meaningful one-sided dot is O(1), so the sweep spans noise → strong.
CUT_LINE_T = (0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)


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


def _peak(dots: list[dict]) -> tuple[float, str | None]:
    """Max maturity across the day and its timestamp. Maturity only rises at a
    dot arrival (masses decay between arrivals), so candidate instants are the
    dot timestamps themselves."""
    best = 0.0
    best_ts: str | None = None
    instants = sorted({str(dot["ts"]) for dot in dots}, key=lambda ts: _parse_ts(ts))
    for now_ts in instants:
        cutoff = _parse_ts(now_ts)
        visible = [dot for dot in dots if _parse_ts(dot["ts"]) <= cutoff]
        maturity = score_decayed(visible, now_ts)["maturity"]
        if maturity > best:
            best = maturity
            best_ts = now_ts
    return best, best_ts


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
            peak_maturity, peak_ts = _peak(dots)
            records.append(
                {
                    "market_day": str(row["market_day"]),
                    "symbol": str(row["symbol"]),
                    "decided_at": str(row["decided_at"]),
                    "decision": str(row["decision"]),
                    "old_score": row["score"],
                    "maturity": state["maturity"],
                    "thesis_bull": state["thesis"]["bull_mass"],
                    "thesis_bear": state["thesis"]["bear_mass"],
                    "conviction": state["thesis"]["conviction"],
                    "contested": state["thesis"]["contested"],
                    "peak_maturity": peak_maturity,
                    "peak_ts": peak_ts,
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
        "New **maturity** = thesis_gross × thesis_conviction (signed decayed "
        "thesis mass magnitude); trigger masses never enter it. Old score = the "
        "undecayed all-day sum stored at decision. Peak = the day's max maturity "
        "and the ET instant it peaked."
    )
    lines.append("")

    for day in days:
        rows = by_day[day]
        lines.append(f"## {day}")
        lines.append("")
        lines.append(
            "| symbol | decision time (ET) | old score | new maturity | "
            "thesis bull | thesis bear | conviction | contested | peak maturity | "
            "peak (ET) | decision |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for rec in sorted(rows, key=lambda r: r["maturity"], reverse=True):
            peak_et = _et(rec["peak_ts"]) if rec["peak_ts"] else "—"
            old = "—" if rec["old_score"] is None else f"{rec['old_score']:.2f}"
            lines.append(
                f"| {rec['symbol']} | {_et(rec['decided_at'])} | {old} | "
                f"{rec['maturity']:.3f} | {rec['thesis_bull']:.3f} | "
                f"{rec['thesis_bear']:.3f} | {rec['conviction']:.3f} | "
                f"{'yes' if rec['contested'] else 'no'} | "
                f"{rec['peak_maturity']:.3f} | {peak_et} | {rec['decision']} |"
            )
        lines.append("")

    # Cut-line: how many symbol-days clear each candidate T (per day + total),
    # scored at the decision instant and — separately — at the day's peak.
    lines.append("## Cut-line — candidate maturity threshold T")
    lines.append("")
    lines.append(
        "At-decision maturity is what a live gate would see the moment it fired; "
        "peak is whether the symbol-day EVER cleared T. Counts are symbol-days."
    )
    lines.append("")
    header = "| T | " + " | ".join(f"{d} (dec / peak)" for d in days) + " | total (dec / peak) |"
    lines.append(header)
    lines.append("|" + "---|" * (len(days) + 2))
    for threshold in CUT_LINE_T:
        cells = []
        tot_dec = tot_peak = 0
        for day in days:
            dec = sum(1 for r in by_day[day] if r["maturity"] >= threshold)
            peak = sum(1 for r in by_day[day] if r["peak_maturity"] >= threshold)
            tot_dec += dec
            tot_peak += peak
            cells.append(f"{dec} / {peak}")
        lines.append(
            f"| {threshold:.2f} | " + " | ".join(cells) + f" | {tot_dec} / {tot_peak} |"
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
