"""Background research job registry (async enqueue, bounded concurrency)."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai.research.graph import run_research
from signals.notify import notify

logger = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = 2
MAX_JOB_EVENTS = 200
DEFAULT_JOB_EVENTS_RETURN = 50
_QUERY_TRUNCATE = 120
_SCALAR_TYPES = (str, int, float, bool, type(None))
JOB_TIMEOUT_BY_EFFORT = {
    "quick": 300,
    "standard": 900,
    "deep": 1800,
    "exhaustive": 3600,
}

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="research")


@dataclass
class ResearchJob:
    id: str
    topic: str
    symbol: str | None
    depth: str
    recipe: str = "single_stock"
    symbols: list[str] | None = None
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


def _slim_progress_payload(payload: dict[str, Any]) -> dict[str, Any]:
    slim: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, _SCALAR_TYPES):
            slim[key] = value
        elif isinstance(value, list):
            if key == "queries":
                slim[key] = [str(item)[:_QUERY_TRUNCATE] for item in value]
            else:
                slim[key] = [
                    item for item in value if isinstance(item, _SCALAR_TYPES)
                ]
    return slim


def append_progress_event(
    events: list[dict[str, Any]],
    phase: str,
    payload: dict[str, Any] | None = None,
    *,
    max_events: int = MAX_JOB_EVENTS,
) -> None:
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "phase": phase,
        **_slim_progress_payload(payload or {}),
    }
    events.append(entry)
    overflow = len(events) - max_events
    if overflow > 0:
        del events[:overflow]


_jobs: dict[str, ResearchJob] = {}
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_lock = asyncio.Lock()


def get_job(job_id: str) -> ResearchJob | None:
    return _jobs.get(job_id)


def list_jobs(*, limit: int = 20) -> list[ResearchJob]:
    jobs = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
    return jobs[:limit]


async def enqueue_research(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
    recipe: str = "single_stock",
    symbols: list[str] | None = None,
) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = ResearchJob(
        id=job_id,
        topic=topic.strip(),
        symbol=symbol.strip().upper() if symbol else None,
        depth=depth,
        recipe=recipe,
        symbols=symbols,
    )
    async with _lock:
        _jobs[job_id] = job
    asyncio.create_task(_run_job(job_id))
    return job_id


def submit_research_job(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
    recipe: str = "single_stock",
    symbols: list[str] | None = None,
) -> str:
    """Sync-safe enqueue for chat tools and blocking API callers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            enqueue_research(
                topic=topic,
                symbol=symbol,
                depth=depth,
                recipe=recipe,
                symbols=symbols,
            )
        )

    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(
                    enqueue_research(
                        topic=topic,
                        symbol=symbol,
                        depth=depth,
                        recipe=recipe,
                        symbols=symbols,
                    )
                )
            ).result()

    return loop.run_until_complete(
        enqueue_research(
            topic=topic,
            symbol=symbol,
            depth=depth,
            recipe=recipe,
            symbols=symbols,
        )
    )


async def cancel_job(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if job is None or job.status in ("completed", "failed", "cancelled"):
        return False
    job.status = "cancelled"
    job.finished_at = datetime.now(UTC).isoformat()
    return True


async def _run_job(job_id: str) -> None:
    job = _jobs.get(job_id)
    if job is None or job.status == "cancelled":
        return

    async with _semaphore:
        job = _jobs.get(job_id)
        if job is None or job.status == "cancelled":
            return
        job.status = "running"
        job.started_at = datetime.now(UTC).isoformat()
        timeout_seconds = JOB_TIMEOUT_BY_EFFORT.get(job.depth, 900)
        cancel_event = threading.Event()
        loop = asyncio.get_running_loop()
        def on_progress(phase: str, payload: dict[str, Any]) -> None:
            append_progress_event(job.events, phase, payload)

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    _executor,
                    lambda: run_research(
                        topic=job.topic,
                        symbol=job.symbol,
                        depth=job.depth,
                        recipe=job.recipe,
                        symbols=job.symbols,
                        cancel_event=cancel_event,
                        on_progress=on_progress,
                    ),
                ),
                timeout=timeout_seconds,
            )
            job.result = result
            job.status = "completed"
            artifact_id = (result or {}).get("artifact_id")
            if artifact_id is not None:
                title = (result.get("report") or {}).get("title") or f"Research ready: {job.topic}"
                try:
                    await notify(
                        kind="research_ready",
                        title=title,
                        body=(result.get("markdown") or "")[:280],
                        severity="info",
                        ref_type="research",
                        ref_id=str(artifact_id),
                        action_url=f"/research?id={artifact_id}",
                        data={"artifact_id": artifact_id, "symbol": job.symbol},
                    )
                except Exception:
                    logger.exception("research_ready notify failed for job %s", job.id)
        except asyncio.TimeoutError:
            cancel_event.set()
            job.status = "failed"
            job.error = f"Research timed out after {timeout_seconds}s"
            logger.error("Research job %s timed out", job_id)
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            logger.exception("Research job %s failed", job_id)
        finally:
            job.finished_at = datetime.now(UTC).isoformat()
