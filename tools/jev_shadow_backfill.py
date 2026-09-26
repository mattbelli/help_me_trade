#!/usr/bin/env python3
"""Replay past attention name-days through the Jev shadow. Read-only on every other table.

The live lane (``research/attention_investigator.py``) only ever writes a shadow row for
a name-day it happens to process — ~93/day, queue-limited. That is a two-week wait to
see anything. But the ledger already holds ~1,260 name-days per session and
``attention_outcomes`` already holds 53k COMPLETED outcomes, so the comparison can be
run backwards: rebuild the facts for past sessions and grade immediately.

    .venv/bin/python tools/jev_shadow_backfill.py --dry-run          # size it, spend 0
    .venv/bin/python tools/jev_shadow_backfill.py --sessions 10      # ~12k calls
    .venv/bin/python tools/jev_shadow_backfill.py --sessions 10 --skip-existing

Re-runnable: ``record()`` is an upsert on ``(symbol, session)``, so an interrupted run
resumes with ``--skip-existing`` and an accidental re-run overwrites its own rows.

``text_model_ok`` is derived, never assumed. For a session the investigator actually
worked, ``attention_queue`` says whether a note was produced; for a session it never
touched the field stays NULL — a replay must not manufacture "Jev answered where prose
failed" rows out of name-days where prose never ran (UNMEASURED ≠ FAILED).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.llm import system_one as so  # noqa: E402
from db import attention_shadow as shadow  # noqa: E402
from db.base import DEFAULT_DB_PATH  # noqa: E402
from db.base import connect as _connect  # noqa: E402
from research.attention_investigator import _facts, _jev_questions  # noqa: E402

# TypeSafe Jev 1.13: $42 per Btok input == $0.042 per Mtok. Output tokens are free.
PRICE_PER_MTOK_INPUT = 0.042
CHARS_PER_TOKEN = 2.56  # measured against the live LULU call (2,652 chars -> 1,036 tok)
PROGRESS_EVERY = 250


def resolve_targets(
    *,
    sessions: int,
    population: str,
    limit: int | None,
    skip_existing: bool,
    db_path: Path,
) -> list[dict[str, Any]]:
    """The (symbol, session, text_model_ok) work list, newest session first."""
    angle_filter = {
        "any-lit": "(abn_lit = 1 OR flow_lit = 1)",
        "both-lit": "(abn_lit = 1 AND flow_lit = 1)",
        "all": "1 = 1",
    }[population]

    with _connect(db_path) as conn:
        picked = [
            row["session"]
            for row in conn.execute(
                "SELECT DISTINCT session FROM attention_daily ORDER BY session DESC LIMIT ?",
                (int(sessions),),
            ).fetchall()
        ]
        if not picked:
            return []
        marks = ",".join("?" for _ in picked)
        rows = conn.execute(
            f"""
            SELECT symbol, session, abn_lit, flow_lit
            FROM attention_daily
            WHERE session IN ({marks}) AND {angle_filter}
            ORDER BY session DESC, symbol
            """,
            picked,
        ).fetchall()
        # One queue row per name-day holds the prose outcome for that session.
        queue_state = {
            (row["symbol"], row["session"]): (row["status"], row["note"])
            for row in conn.execute(
                f"SELECT symbol, session, status, note FROM attention_queue "
                f"WHERE session IN ({marks})",
                picked,
            ).fetchall()
        }
        existing = (
            {
                (row["symbol"], row["session"])
                for row in conn.execute(
                    "SELECT symbol, session FROM attention_jev_shadow WHERE status = 'ok'"
                ).fetchall()
            }
            if skip_existing
            else set()
        )

    out: list[dict[str, Any]] = []
    for row in rows:
        key = (row["symbol"], row["session"])
        if key in existing:
            continue
        queued = queue_state.get(key)
        if queued is None:
            text_model_ok: bool | None = None
        else:
            status, note = queued
            text_model_ok = bool(status == "done" and note)
        out.append(
            {
                "symbol": row["symbol"],
                "session": row["session"],
                "abn_lit": row["abn_lit"],
                "flow_lit": row["flow_lit"],
                "text_model_ok": text_model_ok,
            }
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def estimate(rows: list[dict[str, Any]], *, sample: int, db_path: Path) -> dict[str, Any]:
    """Size the run from REAL facts blocks, so the quote is not a guess.

    Builds the state for the first ``sample`` rows (no API calls) and extrapolates.
    """
    probe = rows[: max(1, min(sample, len(rows)))]
    chars: list[int] = []
    empty = 0
    for row in probe:
        try:
            facts = _facts({"symbol": row["symbol"], "session": row["session"]}, db_path)
        except Exception:
            facts = ""
        if not facts:
            empty += 1
            continue
        chars.append(len(facts))

    questions_chars = len(json.dumps(_jev_questions()))
    mean_chars = (sum(chars) / len(chars)) if chars else 0.0
    per_call_tokens = (mean_chars + questions_chars) / CHARS_PER_TOKEN
    total_tokens = per_call_tokens * len(rows)
    return {
        "rows": len(rows),
        "sampled": len(chars),
        "unreadable_facts_in_sample": empty,
        "mean_facts_chars": round(mean_chars),
        "questions_chars": questions_chars,
        "tokens_per_call": round(per_call_tokens),
        "total_input_tokens": round(total_tokens),
        "estimated_usd": round(total_tokens / 1_000_000 * PRICE_PER_MTOK_INPUT, 4),
    }


async def _process(
    row: dict[str, Any],
    *,
    db_path: Path,
    semaphore: asyncio.Semaphore,
    counters: dict[str, int],
) -> None:
    sym, session = row["symbol"], row["session"]
    text_model_ok = row["text_model_ok"]
    async with semaphore:
        try:
            facts = _facts({"symbol": sym, "session": session}, db_path)
        except Exception as exc:
            shadow.record(
                sym, session, status="error", source="backfill",
                text_model_ok=text_model_ok, detail=f"facts: {exc}"[:400], db_path=db_path,
            )
            counters["no_facts"] += 1
            return
        if not facts:
            shadow.record(
                sym, session, status="error", source="backfill",
                text_model_ok=text_model_ok, detail="no facts for this name-day",
                db_path=db_path,
            )
            counters["no_facts"] += 1
            return

        try:
            resp = await so.asystem_one(facts, _jev_questions())
        except Exception as exc:
            shadow.record(
                sym, session, status="error", source="backfill",
                text_model_ok=text_model_ok, detail=str(exc)[:400], db_path=db_path,
            )
            counters["error"] += 1
            return

        materiality = resp.answers["materiality"]
        shadow.record(
            sym,
            session,
            status="ok",
            source="backfill",
            model=resp.model,
            latency_ms=resp.latency_ms,
            input_tokens=resp.usage.get("input_tokens"),
            output_tokens=resp.usage.get("output_tokens"),
            prompt_chars=len(facts),
            text_model_ok=text_model_ok,
            answers={
                "activity_unusual": resp.noul("activity_unusual"),
                "explained_by_supplied_fact": resp.noul("explained_by_supplied_fact"),
                "materiality": resp.score("materiality"),
                "materiality_norm": so.normalize_score(materiality, 3),
                "evidence_thin": resp.noul("evidence_thin"),
                "driver": resp.choice("driver"),
                "driver_confidence": resp.confidence("driver"),
                "next_check": resp.choice("next_check"),
            },
            db_path=db_path,
        )
        counters["ok"] += 1
        counters["input_tokens"] += int(resp.usage.get("input_tokens") or 0)
        counters["output_tokens"] += int(resp.usage.get("output_tokens") or 0)
        done = counters["ok"] + counters["error"] + counters["no_facts"]
        if done % PROGRESS_EVERY == 0:
            print(f"  ... {done} processed ({counters['ok']} ok, {counters['error']} error)", flush=True)


async def run(
    rows: list[dict[str, Any]], *, db_path: Path, concurrency: int
) -> dict[str, int]:
    counters = {"ok": 0, "error": 0, "no_facts": 0, "input_tokens": 0, "output_tokens": 0}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    await asyncio.gather(
        *(_process(row, db_path=db_path, semaphore=semaphore, counters=counters) for row in rows)
    )
    return counters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=int, default=10, help="how many recent sessions")
    parser.add_argument(
        "--population",
        choices=("any-lit", "both-lit", "all"),
        default="any-lit",
        help="which ledger rows to replay (both-lit mirrors what the live lane sees)",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the row count")
    parser.add_argument("--concurrency", type=int, default=4, help="in-flight calls")
    parser.add_argument("--sample", type=int, default=25, help="rows to size the estimate on")
    parser.add_argument("--skip-existing", action="store_true", help="resume an interrupted run")
    parser.add_argument("--dry-run", action="store_true", help="size it and stop; spends nothing")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if not so.is_configured():
        print("no typesafe_api_key configured", file=sys.stderr)
        return 1

    targets = resolve_targets(
        sessions=args.sessions,
        population=args.population,
        limit=args.limit,
        skip_existing=args.skip_existing,
        db_path=args.db,
    )
    if not targets:
        print("nothing to do (no matching name-days, or all already shadowed)")
        return 0

    estimate_info = estimate(targets, sample=args.sample, db_path=args.db)
    print(json.dumps({"population": args.population, "sessions": args.sessions, **estimate_info}, indent=2))
    if args.dry_run:
        print("\n--dry-run: nothing spent. Drop it to run.")
        return 0

    started = time.monotonic()
    counters = asyncio.run(run(targets, db_path=args.db, concurrency=args.concurrency))
    elapsed = time.monotonic() - started
    spent = counters["input_tokens"] / 1_000_000 * PRICE_PER_MTOK_INPUT
    print(
        json.dumps(
            {
                "ok": counters["ok"],
                "error": counters["error"],
                "no_facts": counters["no_facts"],
                "input_tokens": counters["input_tokens"],
                "output_tokens": counters["output_tokens"],
                "usd_used": round(spent, 4),
                "elapsed_s": round(elapsed, 1),
            },
            indent=2,
        )
    )
    print("\nshadow by status:", shadow.summary(db_path=args.db)["by_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
