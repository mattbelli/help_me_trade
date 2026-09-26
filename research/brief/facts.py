"""Nightly brief, Stage 2a: DeepSeek fact digests per candidate card.

For each board card, the name's stored news (headline + summary) and, when it
reported recently, its Item 2.02 EX-99.1 text go to DeepSeek, which returns
strict JSON: a catalyst class, a two-sentence summary, and facts each citing a
source id. No opinions, no direction. Code then checks every fact's number
appears in the source it cites and drops the ones that don't (counted), so the
specialists read facts, not paraphrase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from research.brief.board import _session_bounds
from research.brief.verify import number_in_source

logger = logging.getLogger(__name__)

MODEL = "deepseek-v4-flash"
CATALYSTS = ("earnings", "guidance", "m&a", "analyst", "product", "legal", "regulatory",
             "macro", "none", "other")
MAX_CONCURRENCY = 4
CALL_TIMEOUT_S = 180
# Reasoning tokens come out of the same budget (deepseek-llm-lessons): a small JSON
# answer still needs room for the thinking in front of it.
# Measured 2026-09-26: 8000 truncated on a 2.4k-char news pack (META), forcing the
# empty-content retry and a 52 s call.
MAX_TOKENS = 16000
NEWS_DAYS = 2
NEWS_LIMIT = 12
RELEASE_DAYS = 5
RELEASE_CHARS = 40_000

_SYSTEM = (
    "You extract FACTS from source documents about one stock. Output strict JSON only:\n"
    '{"catalyst": one of ' + "|".join(CATALYSTS) + ', "summary": "<= 2 sentences, facts only", '
    '"facts": [{"fact": "one sentence", "number": "the key figure exactly as printed in the '
    'source, or null", "source_id": "the id of the source it came from"}]}\n'
    "Rules: no opinions, no predictions, no direction (never say bullish/bearish, likely, "
    "expect). Only what the sources state. At most 8 facts, the most material first. "
    "`number` must be copied from the cited source verbatim (e.g. \"$1.2B\", \"12%\", "
    "\"3,400\"). If the sources say nothing material, catalyst \"none\" and facts []."
)


def gather_sources(
    symbol: str, et_date: date, nxt: date, *, db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, str]]:
    """The name's news (last NEWS_DAYS + et_date) and a recent earnings release body."""
    from db.base import connect
    from signals.earnings_exhibit import latest_releases

    start, _ = _session_bounds(et_date - timedelta(days=NEWS_DAYS))
    _, end = _session_bounds(et_date)
    sources: list[dict[str, str]] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT n.id, n.headline, n.summary, n.source, n.published_at FROM news_items n "
            "JOIN news_symbols ns ON ns.news_id = n.id WHERE ns.symbol = ? "
            "AND n.published_at >= ? AND n.published_at < ? "
            "AND json_array_length(n.symbols) <= 3 ORDER BY n.published_at DESC LIMIT ?",
            (symbol, start, end, NEWS_LIMIT),
        ).fetchall()
    for r in rows:
        sources.append({
            "source_id": f"news:{r['id']}",
            "kind": f"news ({r['source']}, {r['published_at']})",
            "text": f"{r['headline']}\n{r['summary'] or ''}".strip(),
        })
    lo = (et_date - timedelta(days=RELEASE_DAYS)).isoformat()
    for r in latest_releases(symbol, limit=1, with_body=True):
        if lo <= str(r.get("filed_date") or "") <= nxt.isoformat() and r.get("body"):
            sources.append({
                "source_id": f"8k:{r['accession']}",
                "kind": f"8-K 2.02 EX-99.1 ({r.get('filed_date')} {r.get('session') or ''})",
                "text": str(r["body"])[:RELEASE_CHARS],
            })
    return sources


def _parse_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the response")
    obj = json.loads(raw[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("response is not a JSON object")
    return obj


def verify_digest(raw: dict[str, Any], sources: list[dict[str, str]]) -> dict[str, Any]:
    """Keep facts whose source_id exists and whose number appears in that source."""
    by_id = {s["source_id"]: s["text"] for s in sources}
    kept, dropped = [], []
    for f in raw.get("facts") or []:
        if not isinstance(f, dict) or not str(f.get("fact") or "").strip():
            dropped.append({"fact": f, "reason": "malformed"})
            continue
        sid = str(f.get("source_id") or "")
        number = f.get("number")
        number = None if number in (None, "", "null") else str(number)
        if sid not in by_id:
            dropped.append({"fact": f, "reason": "unknown source_id"})
        elif number is not None and not number_in_source(number, by_id[sid]):
            dropped.append({"fact": f, "reason": "number not in cited source"})
        else:
            kept.append({"fact": str(f["fact"]).strip(), "number": number, "source_id": sid})
    catalyst = str(raw.get("catalyst") or "").strip().lower()
    return {
        "catalyst": catalyst if catalyst in CATALYSTS else "other",
        "summary": str(raw.get("summary") or "").strip(),
        "facts": kept,
        "dropped": dropped,
        "sources": [{"source_id": s["source_id"], "kind": s["kind"]} for s in sources],
    }


async def digest_one(llm: Any, symbol: str, sources: list[dict[str, str]]) -> dict[str, Any]:
    from ai.llm.base import LLMMessage

    if not sources:
        return {"catalyst": "none", "summary": "", "facts": [], "dropped": [], "sources": [],
                "note": "no news or earnings text stored"}
    docs = "\n\n".join(
        f"[source_id: {s['source_id']}] {s['kind']}\n{s['text']}" for s in sources
    )
    messages = [
        LLMMessage(role="system", content=_SYSTEM),
        LLMMessage(role="user", content=f"Stock: {symbol}\n\nSources:\n\n{docs}"),
    ]
    raw = await asyncio.wait_for(
        asyncio.to_thread(llm.complete_json, messages, max_tokens=MAX_TOKENS),
        timeout=CALL_TIMEOUT_S,
    )
    return verify_digest(_parse_json(raw), sources)


async def abuild_facts(
    board: dict[str, Any], *, llm: Any = None, limit: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    from ai.llm.factory import get_llm_for_provider
    from ai.llm.mock import MockLLM
    from research.brief.board import next_session

    et_date = date.fromisoformat(board["et_date"])
    nxt = next_session(et_date)
    cards = ((board.get("candidates") or {}).get("cards") or [])[:limit]
    llm = llm if llm is not None else get_llm_for_provider("deepseek", model=MODEL)
    if isinstance(llm, MockLLM):
        return {"et_date": board["et_date"], "error": "deepseek unconfigured", "names": {}}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    # Sources are read serially up front (cheap SQL); only the LLM calls run in
    # parallel. Concurrent reads raced on SQLite locks in the smoke run.
    sources: dict[str, Any] = {}
    for card in cards:
        sym = card["symbol"]
        try:
            sources[sym] = gather_sources(sym, et_date, nxt, db_path=db_path)
        except Exception as exc:  # noqa: BLE001 — one name's failure never sinks the rest
            logger.warning("brief facts: %s sources failed: %s", sym, exc)
            sources[sym] = exc

    async def one(sym: str) -> tuple[str, dict[str, Any]]:
        if isinstance(sources[sym], Exception):
            exc = sources[sym]
            return sym, {"error": f"{type(exc).__name__}: {exc}"}
        async with sem:
            try:
                return sym, await digest_one(llm, sym, sources[sym])
            except Exception as exc:  # noqa: BLE001
                logger.warning("brief facts: %s failed: %s", sym, exc)
                return sym, {"error": f"{type(exc).__name__}: {exc}"}

    results = await asyncio.gather(*(one(c["symbol"]) for c in cards))
    names = dict(results)
    return {
        "et_date": board["et_date"],
        "model": getattr(llm, "model", MODEL),
        "names": names,
        "kept_total": sum(len(d.get("facts") or []) for d in names.values()),
        "dropped_total": sum(len(d.get("dropped") or []) for d in names.values()),
        "errors": sorted(s for s, d in names.items() if "error" in d),
    }


def build_facts(board: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return asyncio.run(abuild_facts(board, **kw))


def render_facts_md(facts: dict[str, Any]) -> str:
    out = [
        f"# Fact digests — {facts.get('et_date')}",
        "",
        f"_DeepSeek ({facts.get('model')}) read each name's stored news and any recent "
        "8-K earnings release and listed facts only; code kept a fact only when its number "
        f"appears in the source it cites. Kept {facts.get('kept_total', 0)}, dropped "
        f"{facts.get('dropped_total', 0)}._",
        "",
    ]
    if facts.get("error"):
        out.append(f"_ERROR: {facts['error']}_")
    for sym, d in (facts.get("names") or {}).items():
        out.append(f"## {sym}")
        if "error" in d:
            out += [f"_digest failed: {d['error']}_", ""]
            continue
        out.append(f"- catalyst: **{d.get('catalyst')}**" + (f" — {d['summary']}" if d.get("summary") else ""))
        if d.get("note"):
            out.append(f"- _{d['note']}_")
        for f in d.get("facts") or []:
            num = f" [{f['number']}]" if f.get("number") else ""
            out.append(f"- {f['fact']}{num} ({f['source_id']})")
        if d.get("dropped"):
            out.append(f"- _{len(d['dropped'])} fact(s) dropped by the source check_")
        out.append("")
    return "\n".join(out) + "\n"


def save_facts(facts: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "facts.json").write_text(json.dumps(facts, indent=1, default=str), encoding="utf-8")
    (out_dir / "facts.md").write_text(render_facts_md(facts), encoding="utf-8")
