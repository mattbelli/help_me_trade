"""Brief delivery: register the judge's picks, save the report, ONE push.

Idempotent per (mode, ET date): the report row (kind evening_brief, dedup_key
nightly_brief / weekly_brief) is the stamp, checked first; registration also dedupes
on the idea ledger's own day-wide cooldown key, so a crash between the two steps
can't double-register. Picks register as brief/nightly or brief/weekly.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from research.brief.arms import MODES

logger = logging.getLogger(__name__)

REPORT_KIND = "evening_brief"
ORIGIN_CONSIDERED = "brief/considered"
DEFAULT_HORIZON_SESSIONS = 5


def _ref_close(board: dict[str, Any], symbol: str) -> str | None:
    for card in (board.get("candidates") or {}).get("cards") or []:
        if card.get("symbol") == symbol:
            close = (card.get("day") or {}).get("close")
            return str(close) if close else None
    return None


def _register(item: dict[str, Any], origin: str, board: dict[str, Any],
              db_path: Path) -> int | None:
    from db.idea_outcomes import register_idea

    symbol = str(item.get("symbol") or "").strip().upper()
    direction = str(item.get("direction") or "").lower()
    if direction not in ("long", "short"):
        return None  # a watch-only name has no direction to grade
    horizon = int(item.get("horizon_sessions") or DEFAULT_HORIZON_SESSIONS)
    common = {
        "origin": origin,
        "signal_family": "nightly_brief",
        "symbol": symbol,
        "direction": direction,
        "thesis": str(item.get("thesis") or item.get("why_not") or ""),
        "bear_case": str(item.get("bear_case") or ""),
        "confidence_label": item.get("confidence"),
        # The board's generated_at: the moment the data was seen, and a stable
        # dedup bucket on a re-run of the same night.
        "as_of": board.get("generated_at"),
        "plan_horizon": f"{horizon}d",
        "source_ref": {"brief": board.get("et_date"), "mode": board.get("mode") or "nightly",
                       "rank": item.get("rank")},
        "db_path": db_path,
    }
    zone = item.get("entry_zone")
    if isinstance(zone, dict) and zone.get("low") and zone.get("high") and item.get("stop") \
            and item.get("targets"):
        return register_idea(entry_zone={"low": str(zone["low"]), "high": str(zone["high"])},
                             stop=str(item["stop"]), targets=[str(t) for t in item["targets"]],
                             grading_basis="plan", **common)
    ref = _ref_close(board, symbol)
    if not ref:
        return None
    return register_idea(entry_zone=None, stop=None, targets=None,
                         grading_basis="horizon_return", entry_ref_price=ref, **common)


def register_picks(picks: dict[str, Any], board: dict[str, Any], *,
                   db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    out: dict[str, Any] = {"registered": [], "skipped": []}
    origin_picks = MODES[board.get("mode") or "nightly"]["origin"]
    for origin, items in ((origin_picks, picks.get("picks") or []),
                          (ORIGIN_CONSIDERED, picks.get("considered") or [])):
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                idea_id = _register(item, origin, board, db_path)
            except Exception:  # noqa: BLE001 — one malformed pick never drops the batch
                logger.exception("brief submit: registering %s failed", item.get("symbol"))
                idea_id = None
            key = "registered" if idea_id is not None else "skipped"
            out[key].append({"symbol": item.get("symbol"), "origin": origin,
                             **({"id": idea_id} if idea_id is not None else {})})
    return out


def push_title(picks: dict[str, Any], et_date: str, mode: str = "nightly") -> str:
    title = MODES[mode]["title"]
    syms = [str(p.get("symbol")) for p in (picks.get("picks") or [])[:3] if p.get("symbol")]
    return f"{title}: {', '.join(syms)}" if syms else f"{title}: {et_date}"


async def submit_brief(brief_dir: Path, *, mode: str = "nightly",
                       db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    from db.reports import report_exists_for_day, save_report
    from research.book_driver import deliver_report

    board = json.loads((brief_dir / "board.json").read_text(encoding="utf-8"))
    board["mode"] = mode
    et_date = board["et_date"]
    dedup_key = MODES[mode]["dedup_key"]
    if report_exists_for_day(REPORT_KIND, date.fromisoformat(et_date), dedup_key=dedup_key,
                             db_path=db_path):
        return {"status": "skipped", "reason": "already_submitted"}
    body = (brief_dir / "brief.md").read_text(encoding="utf-8")
    picks_path = brief_dir / "picks.json"
    picks = json.loads(picks_path.read_text(encoding="utf-8")) if picks_path.exists() else {}
    registration = register_picks(picks, board, db_path=db_path)
    report = save_report(
        REPORT_KIND, "", push_title(picks, et_date, mode), body,
        {"et_date": et_date, "mode": mode, "dedup_key": dedup_key, "status": "ok",
         "picks": picks.get("picks") or [], "considered": picks.get("considered") or [],
         "registered": registration["registered"], "skipped": registration["skipped"]},
        format="markdown", db_path=db_path,
    )
    await deliver_report(report, MODES[mode]["title"], db_path)
    return {"status": "ok", "report_id": report["id"], **registration}
