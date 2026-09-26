"""The deep brief, end to end, in two cadences (research/brief/arms.py MODES).

board (code) -> DeepSeek fact digests -> specialists (production Opus, plus shadow
arms on the weekly, all in parallel) -> Opus judge -> blind grader (weekly) -> number
check -> register ideas + evening_brief report + ONE phone push.

- nightly (weekdays 20:30 ET): flows + stocks, top 3 for tomorrow. Briefs today's
  session; a no-op on a non-trading day. Lands in data/briefs/<date>/.
- weekly (Sunday 18:30 ET): all four specialists + shadows + grader, the week ahead.
  Briefs the last completed session (Friday's, holiday-aware) with the week's lanes,
  the weekend headlines and the futures' reaction. Lands in data/briefs/<date>-weekly/.

run.json records per-step wall time and each Claude session's reported cost/usage.
An empty board or a failed judge sends the ntfy dead-man and exits 2
(tools/nightly_brief.sh alarms on any other nonzero).

Usage:
    python tools/nightly_brief.py                         # nightly, today ET
    python tools/nightly_brief.py --mode weekly           # Friday's session on a weekend
    python tools/nightly_brief.py --date 2026-09-25 --no-deliver --shadow
    python tools/nightly_brief.py --only-board-facts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.brief import arms, verify  # noqa: E402
from research.brief.board import (  # noqa: E402
    BRIEFS_DIR,
    board_is_empty,
    board_key,
    build_board,
    save_board,
)
from research.brief.facts import build_facts, save_facts  # noqa: E402

logger = logging.getLogger("nightly_brief")
_ET = ZoneInfo("America/New_York")
FAILED = 2  # handled failure, dead-man already sent
_rng = random.Random()


def deadman(message: str) -> None:
    import httpx

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    logger.error("DEAD-MAN: %s", message)
    if not topic:
        return
    url = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    try:
        httpx.post(f"{url}/{topic}", content=message.encode(), timeout=15,
                   headers={"Title": "nightly brief FAILED", "Priority": "high"})
    except Exception:  # noqa: BLE001
        logger.exception("ntfy dead-man also failed")


class Run:
    """run.json, rewritten after every step so a crash still leaves the trace."""

    def __init__(self, brief_dir: Path, et_date: str) -> None:
        self.path = brief_dir / "run.json"
        self.data: dict[str, Any] = {"et_date": et_date, "started_at": _now(), "steps": {},
                                     "sessions": {}}

    def step(self, name: str, t0: float, **extra: Any) -> None:
        self.data["steps"][name] = {"wall_s": round(time.monotonic() - t0, 1), **extra}
        self.save()

    def session(self, key: str, meta: dict[str, Any]) -> None:
        self.data["sessions"][key] = {k: v for k, v in meta.items() if k != "tool_texts"}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, default=str), encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- specialists --------------------------------------------------------------------------


def run_arm(spec: str, arm: str, effort: str | None, board: dict[str, Any],
            brief_dir: Path) -> dict[str, Any]:
    t0 = time.monotonic()
    if arm == "deepseek":
        code, out, err = arms._spawn(arms.deepseek_cmd(spec, brief_dir), arms.SESSION_TIMEOUT_S)
        try:
            meta = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            meta = {"ok": False, "error": f"exit {code}: {(err or '').strip()[-500:]}"}
        meta["wall_s"] = round(time.monotonic() - t0, 1)
        return meta
    prompt = arms.specialist_prompt(spec, board["et_date"], board["next_session"], brief_dir,
                                    board.get("mode") or "nightly")
    res = arms.run_claude(prompt, effort or "high", arms.SPECIALIST_TOOLS)
    if res["ok"]:
        res.update(arms.write_arm_outputs(brief_dir, spec, arm, res["text"], res["tool_texts"]))
    res.pop("text", None)
    return res


def run_specialists(board: dict[str, Any], brief_dir: Path, *, mode: str,
                    shadow: bool) -> dict[str, Any]:
    jobs = [(spec, arm, effort) for spec in arms.MODES[mode]["specialists"]
            for arm, effort in arms.arms_for(spec, shadow=shadow)]
    jobs.sort(key=lambda j: j[1] != arms.PRODUCTION_ARM)  # production takes the first slots
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {f"{s}_{a}": pool.submit(run_arm, s, a, e, board, brief_dir) for s, a, e in jobs}
    out = {}
    for key, fut in futures.items():
        try:
            out[key] = fut.result()
        except Exception as exc:  # noqa: BLE001 — a crashed arm is a failed arm, not a dead night
            out[key] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


# --- judge --------------------------------------------------------------------------------


def run_judge(board: dict[str, Any], brief_dir: Path, missing: list[str],
              mode: str) -> dict[str, Any]:
    memos = [brief_dir / f"memo_{s}_{arms.PRODUCTION_ARM}.md"
             for s in arms.MODES[mode]["specialists"] if s not in missing]
    prompt = arms.render_prompt(
        "judge", date=board["et_date"], next_session=board["next_session"],
        brief_dir=str(brief_dir), **arms.judge_values(mode),
        memos=", ".join(f"`{p}`" for p in memos) or "(none — every specialist failed)",
        missing=(f"- **Missing tonight: {', '.join(missing)}** (the specialist session failed). "
                 "Say so in the brief and don't invent that seat's view.\n") if missing else "",
    )
    res = arms.run_claude(prompt, arms.MODES[mode]["judge_effort"], arms.JUDGE_TOOLS)
    if not res["ok"]:
        return res
    brief_md, picks = arms.split_json_block(res["text"])
    brief_md, removed = verify.strip_probability_claims(brief_md)
    if missing:
        brief_md = (f"_Tonight's brief ran without the {', '.join(missing)} specialist "
                    f"memo (session failed)._\n\n{brief_md}")
    if not brief_md.strip():
        return {**res, "ok": False, "error": "judge returned an empty brief"}
    (brief_dir / "brief.md").write_text(brief_md + "\n", encoding="utf-8")
    (brief_dir / "picks.json").write_text(
        json.dumps(picks if isinstance(picks, dict) else {"picks": [], "considered": []}, indent=1),
        encoding="utf-8")
    (brief_dir / "tools_judge.txt").write_text("\n\n".join(res["tool_texts"]), encoding="utf-8")
    res.pop("text", None)
    return {**res, "probability_sentences_removed": removed,
            "picks_parsed": isinstance(picks, dict)}


# --- grader -------------------------------------------------------------------------------


def _parse_verdict(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    try:
        obj = json.loads(text[start:end + 1]) if start >= 0 else None
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and obj.get("winner") in ("A", "B", "tie") else None


def plan_grades(brief_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Blind pairs (production vs each shadow arm with a memo), labels shuffled by code.
    Writes each pair's input file and persists the label->arm map in grades.json."""
    grades: dict[str, list[dict[str, Any]]] = {}
    for spec in arms.SPECIALISTS:
        prod = brief_dir / f"memo_{spec}_{arms.PRODUCTION_ARM}.md"
        if not prod.exists():
            continue
        for i, shadow in enumerate(arms.SHADOW_ARMS):
            other = brief_dir / f"memo_{spec}_{shadow}.md"
            if not other.exists():
                continue
            pair = [arms.PRODUCTION_ARM, shadow]
            _rng.shuffle(pair)
            labels = {"A": pair[0], "B": pair[1]}
            inp = brief_dir / f"grade_input_{spec}_{i}.md"
            inp.write_text(
                "\n\n".join(
                    f"# Memo {label}\n\n"
                    + (brief_dir / f"memo_{spec}_{arm}.md").read_text(encoding="utf-8")
                    for label, arm in labels.items()),
                encoding="utf-8")
            grades.setdefault(spec, []).append(
                {"shadow": shadow, "input": inp.name, "labels": labels, "verdict": None})
    (brief_dir / "grades.json").write_text(json.dumps(grades, indent=1), encoding="utf-8")
    return grades


def run_grader(board: dict[str, Any], brief_dir: Path, run: Run) -> dict[str, Any]:
    grades = plan_grades(brief_dir)
    games = [(spec, g) for spec, gs in grades.items() for g in gs]

    def one(spec: str, g: dict[str, Any]) -> dict[str, Any]:
        prompt = arms.render_prompt("grader", specialist=spec, date=board["et_date"],
                                    brief_dir=str(brief_dir), input=str(brief_dir / g["input"]))
        return arms.run_claude(prompt, arms.GRADER_EFFORT, arms.GRADER_TOOLS)

    if games:
        with ThreadPoolExecutor(max_workers=len(games)) as pool:
            results = list(pool.map(lambda sg: one(*sg), games))
        for (spec, g), res in zip(games, results, strict=True):
            g["verdict"] = _parse_verdict(res.get("text") or "") if res["ok"] else None
            if res["ok"] and g["verdict"] is None:
                res = {**res, "ok": False, "error": "unparseable verdict"}
            res.pop("text", None)
            run.session(f"grade_{spec}_{g['shadow']}", res)
    (brief_dir / "grades.json").write_text(json.dumps(grades, indent=1), encoding="utf-8")
    return grades


# --- main ---------------------------------------------------------------------------------


def session_date(mode: str, now_et: datetime) -> date | None:
    """The session a run briefs. Nightly: today, None on a non-trading day. Weekly: the
    last completed session (Friday's on a Sunday; Thursday's after a Friday holiday)."""
    from trading.market_calendar import is_trading_day, last_closed_session

    if mode == "weekly":
        return last_closed_session(now_et)
    return now_et.date() if is_trading_day(now_et.date()) else None


def night(et_date: date, a: argparse.Namespace) -> int:
    mode = a.mode
    shadow = arms.MODES[mode]["shadow"] or a.shadow
    brief_dir = BRIEFS_DIR / board_key(et_date.isoformat(), mode)
    run = Run(brief_dir, et_date.isoformat())
    run.data["options"] = {"mode": mode, "deliver": not a.no_deliver, "shadow": shadow,
                           "only_board_facts": a.only_board_facts}
    what = f"{et_date} {mode}"

    t0 = time.monotonic()
    board = build_board(et_date, mode=mode)
    save_board(board, root=BRIEFS_DIR)
    cards = len((board.get("candidates") or {}).get("cards") or [])
    run.step("board", t0, candidates=cards)
    if board_is_empty(board):
        deadman(f"{what}: the brief board is empty (no candidates, no index frame)")
        return FAILED

    t0 = time.monotonic()
    facts = build_facts(board)
    save_facts(facts, brief_dir)
    run.step("facts", t0, kept=facts.get("kept_total"), dropped=facts.get("dropped_total"),
             errors=facts.get("errors") or facts.get("error"))
    if a.only_board_facts:
        return 0

    t0 = time.monotonic()
    sessions = run_specialists(board, brief_dir, mode=mode, shadow=shadow)
    for key, meta in sessions.items():
        run.session(key, meta)
    missing = [s for s in arms.MODES[mode]["specialists"]
               if not sessions.get(f"{s}_{arms.PRODUCTION_ARM}", {}).get("ok")]
    run.step("specialists", t0, missing_production=missing)

    t0 = time.monotonic()
    judge = run_judge(board, brief_dir, missing, mode)
    run.session("judge", judge)
    run.step("judge", t0, ok=judge["ok"])
    if not judge["ok"]:
        deadman(f"{what}: the brief judge failed: {judge.get('error')}")
        return FAILED

    if shadow:
        t0 = time.monotonic()
        run_grader(board, brief_dir, run)
        run.step("grader", t0)

    t0 = time.monotonic()
    numbers = verify.check_night(brief_dir, arms.PRODUCTION_ARM)
    unmatched = (numbers.get("brief") or {}).get("unmatched") or []
    if unmatched:
        brief = brief_dir / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8").rstrip() + verify.footer(unmatched),
                         encoding="utf-8")
    run.step("number_check", t0, brief_unmatched=len(unmatched))

    if not a.no_deliver:
        from research.brief.submit import submit_brief

        t0 = time.monotonic()
        result = asyncio.run(submit_brief(brief_dir, mode=mode))
        run.step("submit", t0, **result)
    run.data["finished_at"] = _now()
    run.save()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=tuple(arms.MODES), default="nightly")
    p.add_argument("--date", default=None,
                   help="ET session YYYY-MM-DD (default: today for nightly, the last "
                        "completed session for weekly)")
    p.add_argument("--no-deliver", action="store_true", help="skip register/save/push")
    p.add_argument("--shadow", action="store_true",
                   help="also run the A/B shadow arms + blind grader (off by default)")
    p.add_argument("--only-board-facts", action="store_true", help="stop after the fact digests")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from trading.market_calendar import is_trading_day

    now_et = datetime.now(_ET)
    et_date = date.fromisoformat(a.date) if a.date else session_date(a.mode, now_et)
    if et_date is None or not is_trading_day(et_date):
        logger.info("%s is not a trading day; nothing to brief", et_date or now_et.date())
        return 0
    try:
        return night(et_date, a)
    except Exception as exc:  # noqa: BLE001 — unattended: every crash pages
        logger.exception("%s brief crashed", a.mode)
        deadman(f"{et_date} {a.mode}: brief crashed: {type(exc).__name__}: {exc}")
        return FAILED


if __name__ == "__main__":
    raise SystemExit(main())
