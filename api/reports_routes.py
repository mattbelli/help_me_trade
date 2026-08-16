"""Reports: the scheduler loop (morning levels + settings-driven kinds), generation by
kind, "report ready" push, and the list/get/generate routes.

Extracted verbatim from ``api/main.py`` (2026-08-16 god-module split). ``api/main``
includes ``router`` and starts ``_reports_scheduler_loop`` from ``lifespan``; the
heartbeat job registration reads ``MORNING_REPORT_INTERVAL_SECONDS`` /
``_MORNING_REPORT_ENV_ENABLED`` from here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from api.jobs import mark_alive
from config.settings import SCHEDULABLE_REPORT_KINDS, reports_config
from trading.market_hours import _ET

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reports"])

MORNING_REPORT_INTERVAL_SECONDS = max(
    60, int(os.environ.get("MORNING_REPORT_INTERVAL_SECONDS", "900"))
)
_MORNING_REPORT_ENV_ENABLED = os.environ.get("MORNING_REPORT_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
MORNING_REPORT_SYMBOL = "SPY"


def _morning_report_window(now_et: datetime, hour_et: int) -> bool:
    """True across a ~2-hour morning window from the scheduled hour (ET). Wide enough
    that the 15-min poll always lands inside it regardless of boot timing (a 14-min
    slot could be missed entirely); the per-ET-day dedup still ensures one send."""
    return hour_et <= now_et.hour < hour_et + 2


def _morning_report_enabled() -> bool:
    cfg = reports_config()
    return _MORNING_REPORT_ENV_ENABLED and bool(cfg.get("morning_levels_enabled", True))


async def _morning_levels_report_once() -> None:
    """Generate and push the SPY morning levels report once per ET day."""
    from db.reports import morning_levels_exists_for_day
    from signals.notify import notify
    from signals.reports import generate_morning_levels_report

    now_et = datetime.now(_ET)
    hour_et = int(reports_config().get("hour_et", 8))
    if not _morning_report_window(now_et, hour_et):
        return

    today = now_et.date()
    symbol = MORNING_REPORT_SYMBOL
    if morning_levels_exists_for_day(symbol, today):
        return

    try:
        report = await generate_morning_levels_report(symbol, et_date=today)
    except Exception:
        logger.exception("Morning levels report: compute/save failed")
        return
    if report is None:
        logger.info("Morning levels report: no session data for %s", symbol)
        return

    try:
        await notify(
            kind="report_ready",
            title=report["title"],
            body=report["body"],
            severity="info",
            ref_type="report",
            ref_id=report["id"],
            action_url=f"/reports/{report['id']}",
            data={"report_id": report["id"], "kind": "morning_levels"},
        )
    except Exception:
        logger.exception("Morning levels report: push failed for %s", report["id"])
    else:
        logger.info(
            "Morning levels report sent: id=%s symbol=%s et_date=%s",
            report["id"],
            symbol,
            today.isoformat(),
        )


def _report_push_body(report: dict) -> str:
    """Plaintext push body: HTML briefs push their summary, not the raw markup."""
    if report.get("format") == "html":
        rj = report.get("payload", {}).get("report") or {}
        summary = str(rj.get("summary") or "").strip()
        return summary[:280] or str(report.get("title", "Report ready"))
    return str(report.get("body", ""))


async def _notify_report_ready(report: dict, *, push: bool | None = None) -> None:
    from signals.notify import notify

    kwargs: dict[str, bool] = {}
    if push is not None:
        kwargs["push"] = push
    await notify(
        kind="report_ready",
        title=report["title"],
        body=_report_push_body(report),
        severity="info",
        ref_type="report",
        ref_id=report["id"],
        action_url=f"/reports/{report['id']}",
        data={"report_id": report["id"], "kind": report["kind"]},
        **kwargs,
    )


async def _generate_report_by_kind(
    kind: str,
    *,
    symbols: list[str] | None = None,
    topic: str | None = None,
    et_date: date | None = None,
) -> dict | None:
    """Dispatch a report kind to its generator (shared by scheduler + API)."""
    from signals import reports as reports_mod

    syms = symbols or None
    if kind == "morning_levels":
        sym = syms[0] if syms else MORNING_REPORT_SYMBOL
        return await reports_mod.generate_morning_levels_report(sym, et_date=et_date)
    if kind == "levels":
        return await reports_mod.generate_levels_report(
            syms or [MORNING_REPORT_SYMBOL], et_date=et_date
        )
    if kind == "morning_brief":
        return await reports_mod.generate_morning_brief_report(syms, et_date=et_date)
    if kind == "evening_brief":
        return await reports_mod.generate_evening_brief_report(syms, et_date=et_date)
    if kind == "on_request":
        return await reports_mod.generate_on_request_report(topic or "", syms, et_date=et_date)
    if kind == "flow_decomp":
        return await reports_mod.generate_flow_decomp_report(et_date=et_date)
    if kind == "overnight_flow":
        return await reports_mod.generate_overnight_flow_report(et_date=et_date)
    return None


async def _run_scheduled_report(job: dict) -> None:
    """Generate + push one configured report job, once per (kind, params, ET-day)."""
    from db.reports import report_exists_for_day

    kind = str(job.get("kind", "")).strip()
    if not job.get("enabled") or kind not in SCHEDULABLE_REPORT_KINDS:
        return

    now_et = datetime.now(_ET)
    if not _morning_report_window(now_et, int(job.get("hour_et", 8))):
        return

    today = now_et.date()
    symbols = [str(s).strip().upper() for s in (job.get("symbols") or []) if str(s).strip()]
    dedup_key = ",".join(symbols) if kind == "levels" else kind
    if report_exists_for_day(kind, today, dedup_key=dedup_key):
        return

    try:
        report = await _generate_report_by_kind(kind, symbols=symbols, et_date=today)
    except Exception:
        logger.exception("Scheduled report generation failed: kind=%s", kind)
        return
    if report is None:
        logger.info("Scheduled report produced nothing: kind=%s", kind)
        return

    try:
        await _notify_report_ready(report)
    except Exception:
        logger.exception("Scheduled report push failed: id=%s", report["id"])
    else:
        logger.info("Scheduled report sent: kind=%s id=%s", kind, report["id"])


async def _reports_scheduler_loop() -> None:
    """Poll every ~15 min; run the legacy morning-levels job plus any configured
    report jobs (morning/evening briefs, extra levels) during their ET windows.

    Env gates:
      MORNING_REPORT_ENABLED              default "1"  (set to "0" to disable all)
      MORNING_REPORT_INTERVAL_SECONDS     default 900
      MORNING_REPORT_HOUR_ET              default 8 (settings hour_et also applies)
    """
    while True:
        await asyncio.sleep(MORNING_REPORT_INTERVAL_SECONDS)
        if not _MORNING_REPORT_ENV_ENABLED:
            continue
        try:
            if _morning_report_enabled():
                try:
                    await _morning_levels_report_once()
                except Exception:
                    logger.exception("Morning levels report loop iteration failed")
            for job in reports_config().get("jobs", []):
                try:
                    await _run_scheduled_report(job)
                except Exception:
                    logger.exception("Scheduled report job failed: %s", job.get("kind"))
        except Exception:
            # Guards the config reads too — this loop must never die (audit LOW-7).
            logger.exception("Reports scheduler loop iteration failed")
        else:
            mark_alive("reports_scheduler")


@router.get("/api/reports")
def api_list_reports(limit: int = 30, kind: str | None = None):
    from db.reports import list_reports, recent_reports_by_kind

    cap = max(1, min(int(limit), 100))
    if kind:
        return {"reports": recent_reports_by_kind(kind.strip(), limit=cap)}
    return {"reports": list_reports(limit=cap)}


@router.get("/api/reports/{report_id}")
def api_get_report(report_id: str):
    from db.reports import get_report

    report = get_report(report_id.strip())
    if report is None:
        return JSONResponse({"error": "Report not found"}, status_code=404)
    return report


_GENERATABLE_REPORT_KINDS = (
    "morning_levels",
    "levels",
    "morning_brief",
    "evening_brief",
    "on_request",
    "flow_decomp",
)


@router.post("/api/reports/generate")
async def api_generate_report(body: dict = Body(...), push: bool = False):
    kind = str(body.get("kind", "")).strip() or "morning_levels"
    if kind not in _GENERATABLE_REPORT_KINDS:
        return JSONResponse(
            {
                "error": f"Unsupported kind '{kind}'",
                "supported": list(_GENERATABLE_REPORT_KINDS),
            },
            status_code=400,
        )

    # symbols: accept `symbols` (list) or legacy single `symbol`.
    raw_symbols = body.get("symbols")
    if isinstance(raw_symbols, list):
        symbols = [str(s).strip().upper() for s in raw_symbols if str(s).strip()]
    else:
        one = str(body.get("symbol", "")).strip().upper()
        symbols = [one] if one else []
    if kind in ("morning_levels", "levels") and not symbols:
        symbols = [MORNING_REPORT_SYMBOL]
    topic = str(body.get("topic", "")).strip()
    if kind == "on_request" and not topic:
        return JSONResponse({"error": "kind 'on_request' requires a 'topic'"}, status_code=400)

    try:
        report = await _generate_report_by_kind(kind, symbols=symbols or None, topic=topic or None)
    except Exception as exc:
        logger.exception("Manual report generate failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    if report is None:
        return JSONResponse({"error": "No data available for this report"}, status_code=404)

    try:
        await _notify_report_ready(report, push=push)
    except Exception:
        logger.exception("Manual report notify failed for %s", report["id"])
    return report
