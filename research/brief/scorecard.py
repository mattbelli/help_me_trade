"""Brief A/B scorecard: which model/effort writes the better specialist memo.

Reads both layouts: data/briefs/<date>/ (nightly lite: production arms only, no
grades) and data/briefs/<date>-weekly/ (every arm, graded). Games and win% come
from the weekly runs alone; `graded` counts those, so a nightly run is never a
"night without grades". Three measures per specialist x arm:
- the blind grader's pairwise verdicts (grades.json),
- numbers printed that aren't in our data (numbers.json),
- forward calls (calls_<spec>_<arm>.json) resolved by code against our daily bars
  (`close`) or data/macro.db (`series`) once their horizon has passed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.brief.board import BRIEFS_DIR

_ET = ZoneInfo("America/New_York")
SCORE_KEYS = ("accuracy", "depth", "insight", "actionability")


def target_session(et_date: date, horizon_sessions: int) -> date:
    from research.brief.board import next_session

    d = et_date
    for _ in range(horizon_sessions):
        d = next_session(d)
    return d


def resolve_call(
    call: dict[str, Any], et_date: date, *, closes: Any, series: Any, today: date | None = None,
) -> dict[str, Any]:
    """hit | miss | pending | no_data. `closes(symbol, day) -> float|None`;
    `series(series_id, after, through) -> (value|None, newest_date|None)`."""
    target = target_session(et_date, int(call["horizon_sessions"]))
    today = today or datetime.now(_ET).date()
    # A session's close is final once the day is over; resolve the morning after.
    if target >= today:
        return {"status": "pending", "target": target.isoformat()}
    if call["kind"] == "close":
        value = closes(call["subject"], target)
    else:
        value, newest = series(call["subject"], et_date, target)
        # FRED posts a day or more late: until the store holds the target date (or a
        # week has passed), the last value before it isn't the answer yet.
        if (newest or "") < target.isoformat() and (today - target).days < 7:
            return {"status": "pending", "target": target.isoformat()}
    if value is None:
        return {"status": "no_data", "target": target.isoformat()}
    hit = value > call["threshold"] if call["op"] == ">" else value < call["threshold"]
    return {"status": "hit" if hit else "miss", "target": target.isoformat(), "value": value}


def daily_close(symbol: str, day: date) -> float | None:
    from research.bars import get_daily_bars

    for bar in get_daily_bars(symbol, day, day):
        if bar["d"] == day.isoformat():
            return float(bar["c"])
    return None


def macro_value(series_id: str, after: date, through: date) -> tuple[float | None, str | None]:
    """(the last observation in (after, through] or None, the series' newest date overall)."""
    from db.base import connect
    from db.macro import DEFAULT_MACRO_DB_PATH

    with connect(DEFAULT_MACRO_DB_PATH) as conn:
        row = conn.execute(
            "SELECT value FROM macro_series WHERE series = ? AND date > ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (series_id, after.isoformat(), through.isoformat()),
        ).fetchone()
        newest = conn.execute(
            "SELECT MAX(date) AS d FROM macro_series WHERE series = ?", (series_id,)
        ).fetchone()
    return (float(row["value"]) if row else None), (newest["d"] if newest else None)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def collect(root: Path = BRIEFS_DIR, *, closes: Any = daily_close, series: Any = macro_value,
            today: date | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
        "nights": set(), "graded": set(), "games": 0, "wins": 0, "ties": 0,
        "scores": defaultdict(list), "unmatched": [], "hits": 0, "resolved": 0,
        "pending": 0, "wall_s": [],
    })
    for night in sorted(p for p in root.glob("*") if p.is_dir()):
        try:
            et_date = date.fromisoformat(night.name.removesuffix("-weekly"))
        except ValueError:
            continue
        for path in night.glob("calls_*.json"):
            spec, arm = path.stem.removeprefix("calls_").split("_", 1)
            r = rows[(spec, arm)]
            r["nights"].add(night.name)
            for call in _load(path) or []:
                res = resolve_call(call, et_date, closes=closes, series=series, today=today)
                if res["status"] in ("hit", "miss"):
                    r["resolved"] += 1
                    r["hits"] += res["status"] == "hit"
                elif res["status"] == "pending":
                    r["pending"] += 1
        for key, m in ((_load(night / "numbers.json") or {}).get("memos") or {}).items():
            spec, arm = key.split("_", 1)
            rows[(spec, arm)]["unmatched"].append(len(m.get("unmatched") or []))
        for spec, games in (_load(night / "grades.json") or {}).items():
            for g in games:
                labels, verdict = g.get("labels") or {}, g.get("verdict") or {}
                if not labels or not verdict:
                    continue
                winner = verdict.get("winner")
                for label, arm in labels.items():
                    r = rows[(spec, arm)]
                    r["graded"].add(night.name)
                    r["games"] += 1
                    r["wins"] += winner == label
                    r["ties"] += winner == "tie"
                    for k in SCORE_KEYS:
                        v = (verdict.get(label) or {}).get(k)
                        if isinstance(v, (int, float)):
                            r["scores"][k].append(v)
        for key, s in (((_load(night / "run.json") or {}).get("sessions")) or {}).items():
            if "_" in key and s.get("wall_s") is not None and key.split("_", 1)[0] != "grade":
                spec, arm = key.split("_", 1)
                rows[(spec, arm)]["wall_s"].append(s["wall_s"])
    return rows


def _mean(xs: list[float]) -> str:
    return f"{sum(xs) / len(xs):.1f}" if xs else "—"


def render(rows: dict[tuple[str, str], dict[str, Any]]) -> str:
    from research.ev import wilson_ci

    lines = [
        "Brief A/B scorecard (nightly + weekly runs)",
        "  runs: nights the arm wrote a memo; graded: weekly runs with blind games (only the",
        "        weekly runs shadows); win%: share of those games won (ties count as not won)",
        "  hit%: forward calls that came true, with the plausible range (95% Wilson) — with",
        "        few calls that range is wide; don't read a gap inside it as a difference",
        "",
        f"{'specialist':<10} {'arm':<11} {'runs':>6} {'graded':>6} {'games':>5} {'win%':>5} "
        f"{'acc':>4} {'dep':>4} {'ins':>4} {'act':>4} {'unmatched/memo':>14} "
        f"{'calls':>5} {'hit%':>5} {'range':>11} {'pend':>4} {'wall_s':>7}",
    ]
    for (spec, arm), r in sorted(rows.items()):
        win = f"{100 * r['wins'] / r['games']:.0f}" if r["games"] else "—"
        if r["resolved"]:
            lo, hi = wilson_ci(r["hits"], r["resolved"])
            hit, rng = f"{100 * r['hits'] / r['resolved']:.0f}", f"{100 * lo:.0f}–{100 * hi:.0f}%"
        else:
            hit, rng = "—", "—"
        lines.append(
            f"{spec:<10} {arm:<11} {len(r['nights']):>6} {len(r['graded']):>6} {r['games']:>5} "
            f"{win:>5} "
            + " ".join(f"{_mean(r['scores'][k]):>4}" for k in SCORE_KEYS)
            + f" {_mean(r['unmatched']):>14} {r['resolved']:>5} {hit:>5} {rng:>11} "
            f"{r['pending']:>4} {_mean(r['wall_s']):>7}"
        )
    return "\n".join(lines)
