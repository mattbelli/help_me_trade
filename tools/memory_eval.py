"""Memory eval harness — the golden-set gate (design §8).

Runs ``tests/fixtures/memory_golden.json`` against the *configured* LLM (live) and
prints the §8 acceptance bars: junk precision (zero junk stored — non-negotiable),
extraction recall, echo suppression, and judge action accuracy. Retrieval hit@4 is
carried in the golden set but deferred to Phase 2 (hybrid retriever + embeddings).

Everything runs against throwaway temp DBs — never the live ``data/trading.db``.

Usage:
  .venv/bin/python tools/memory_eval.py            # against the configured LLM
Offline (no provider configured) it runs against the deterministic MockLLM, which
ADD-alls the judge — useful only to prove the harness itself runs end-to-end.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "memory_golden.json"

# §8 acceptance bars.
JUNK_REQUIRED = 12
ECHO_REQUIRED = 6
EXTRACTION_RECALL_MIN = 8
JUDGE_ACCURACY_MIN = 0.90
# Retrieval hit@4 floors (measured on the real retriever + golden set: lexical
# 9/15, hybrid 12/15). The hybrid must clear HYBRID_MIN *and* strictly beat the
# same run's lexical baseline (proves the embeddings add value, not just no-regress).
# HYBRID_MIN sits one below the measured 12 for margin (a cosine tie / model
# republish shouldn't red a healthy gate). When embeddings are unavailable the gate
# falls back to the lexical floor so an embeddings-less box doesn't spuriously fail.
RETRIEVAL_HIT4_MIN = 11
RETRIEVAL_LEXICAL_MIN = 8


def load_golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text())


def _fresh_db(tmp: Path, name: str) -> Path:
    """Point the app's default DB at a fresh file and reset the memory singleton."""
    import ai.memories as mem_mod
    import db.ai_tables as ai_tables
    import db.base as db_base

    db = tmp / f"{name}.db"
    db_base.DEFAULT_DB_PATH = db
    mem_mod._memory_service = None
    ai_tables.init_ai_tables(db)
    return db


def _run_extraction(tmp: Path, golden: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from ai.chat.memory_extract import extract_memories_from_turn
    from db.ai_tables import create_memory

    ext = golden["extraction"]
    out: dict[str, dict[str, Any]] = {}

    # Junk — expect ZERO stored candidates.
    junk_fails: list[str] = []
    for i, case in enumerate(ext["junk"]):
        _fresh_db(tmp, f"junk_{i}")
        saved = extract_memories_from_turn(
            user_text=case["user"],
            assistant_text=case["assistant"],
            session_id="eval",
        )
        if saved:
            junk_fails.append(f"{case['user'][:48]!r} -> {[m['content'] for m in saved]}")
    out["junk"] = {"pass": len(ext["junk"]) - len(junk_fails), "total": len(ext["junk"]), "fails": junk_fails}

    # Legit — expect a candidate of the right type containing the verbatim numbers/words.
    legit_fails: list[str] = []
    for i, case in enumerate(ext["legit"]):
        _fresh_db(tmp, f"legit_{i}")
        saved = extract_memories_from_turn(
            user_text=case["user"],
            assistant_text=case["assistant"],
            session_id="eval",
        )
        ok = any(
            m.get("type") == case["expect_type"]
            and all(sub.lower() in m.get("content", "").lower() for sub in case["must_contain"])
            for m in saved
        )
        if not ok:
            legit_fails.append(f"{case['user'][:48]!r} -> {[m.get('content') for m in saved]}")
    out["legit"] = {"pass": len(ext["legit"]) - len(legit_fails), "total": len(ext["legit"]), "fails": legit_fails}

    # Echo — a known memory restated must NOT be re-extracted.
    echo_fails: list[str] = []
    for i, case in enumerate(ext["echo"]):
        _fresh_db(tmp, f"echo_{i}")
        create_memory(memory_type=case["seed"]["type"], content=case["seed"]["content"])
        saved = extract_memories_from_turn(
            user_text=case["user"],
            assistant_text=case["assistant"],
            session_id="eval",
        )
        if saved:
            echo_fails.append(f"{case['user'][:48]!r} -> {[m['content'] for m in saved]}")
    out["echo"] = {"pass": len(ext["echo"]) - len(echo_fails), "total": len(ext["echo"]), "fails": echo_fails}
    return out


def _run_judge(tmp: Path, golden: dict[str, Any], *, llm: Any | None) -> dict[str, Any]:
    from ai.memories.judge import Candidate, judge_and_store
    from db.ai_tables import create_memory

    fails: list[str] = []
    cases = golden["judge"]
    for i, case in enumerate(cases):
        db = _fresh_db(tmp, f"judge_{i}")
        for existing in case.get("existing", []):
            create_memory(
                memory_type=existing["type"],
                content=existing["content"],
                authority=existing.get("authority", "agent_inferred"),
                db_path=db,
            )
        c = case["candidate"]
        cand = Candidate(
            memory_type=c["type"],
            content=c["content"],
            quote=c.get("quote"),
            source=c.get("source", "chat_extract"),
            authority=c.get("authority", "agent_inferred"),
        )
        stored = judge_and_store([cand], db_path=db, llm=llm)
        expected = case["expect_action"]
        if expected == "NOOP":
            actual = "NOOP" if not stored else stored[0].get("action")
        else:
            actual = stored[0].get("action") if stored else "NONE"
        if actual != expected:
            fails.append(f"{case['name']}: expected {expected}, got {actual}")
    return {"pass": len(cases) - len(fails), "total": len(cases), "fails": fails}


def _run_retrieval(tmp: Path, golden: dict[str, Any]) -> dict[str, Any]:
    """Seed the canonical corpus into a fresh DB and score the golden queries with
    the REAL retriever (hybrid if embeddings are on, lexical otherwise). hit@k = a
    top-k result's content contains the case's `expect_contains` substring."""
    from ai.memories.retrieval import retrieve_relevant_memories
    from db.ai_tables import create_memory

    corpus = golden.get("retrieval_corpus", [])
    cases = golden.get("retrieval", [])
    if not corpus or not cases:
        return {"hit1": 0, "hit4": 0, "total": 0, "fails": []}

    _fresh_db(tmp, "retrieval")
    for mem in corpus:
        create_memory(memory_type=mem["type"], content=mem["content"])

    hit1 = hit4 = 0
    fails: list[str] = []
    for case in cases:
        results = retrieve_relevant_memories(case["query"], limit=4)
        contents = [str(r.get("content", "")).lower() for r in results]
        sub = case["expect_contains"].lower()
        if contents and sub in contents[0]:
            hit1 += 1
        if any(sub in c for c in contents):
            hit4 += 1
        else:
            fails.append(
                f"{case['query']!r} (want {case['expect_contains']!r}) -> "
                f"{[c[:36] for c in contents]}"
            )
    return {"hit1": hit1, "hit4": hit4, "total": len(cases), "fails": fails}


def _retrieval_lexical_vs_hybrid(
    golden: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Run the retrieval golden set lexical-only then hybrid (own temp DBs each).
    Returns (lexical_result, hybrid_result, embeddings_actually_on)."""
    import os

    import ai.memories as mem_mod
    import ai.memories.embedding as emb
    import db.base as db_base

    saved_path, saved_service = db_base.DEFAULT_DB_PATH, mem_mod._memory_service
    saved_flag = os.environ.get("MEMORY_EMBEDDINGS_ENABLED")

    def _mode(enabled: str) -> dict[str, Any]:
        os.environ["MEMORY_EMBEDDINGS_ENABLED"] = enabled
        emb.reset_cache()
        with tempfile.TemporaryDirectory() as td:
            return _run_retrieval(Path(td), golden)

    try:
        lex = _mode("0")
        hyb = _mode("1")
        embeddings_on = emb.is_available()  # true only if flag on AND model loaded
    finally:
        db_base.DEFAULT_DB_PATH = saved_path
        mem_mod._memory_service = saved_service
        if saved_flag is None:
            os.environ.pop("MEMORY_EMBEDDINGS_ENABLED", None)
        else:
            os.environ["MEMORY_EMBEDDINGS_ENABLED"] = saved_flag
        emb.reset_cache()
    return lex, hyb, embeddings_on


def run_eval(golden: dict[str, Any], *, llm: Any | None = None) -> dict[str, Any]:
    """Run the Phase-1 golden set. ``llm`` (if given) is passed to the judge; the
    extractor always uses the configured provider via ``get_llm``. The global
    default DB path + memory singleton are saved and restored so this is safe to
    call from tests."""
    import ai.memories as mem_mod
    import db.base as db_base

    saved_path = db_base.DEFAULT_DB_PATH
    saved_service = mem_mod._memory_service
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            results = _run_extraction(tmp, golden)
            results["judge"] = _run_judge(tmp, golden, llm=llm)
        return results
    finally:
        db_base.DEFAULT_DB_PATH = saved_path
        mem_mod._memory_service = saved_service


def _bar(label: str, ok: bool, detail: str) -> str:
    return f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}"


def main() -> int:
    golden = load_golden()
    results = run_eval(golden)

    junk = results["junk"]
    legit = results["legit"]
    echo = results["echo"]
    judge = results["judge"]
    judge_acc = judge["pass"] / judge["total"] if judge["total"] else 0.0

    junk_ok = junk["pass"] == JUNK_REQUIRED
    echo_ok = echo["pass"] == ECHO_REQUIRED
    recall_ok = legit["pass"] >= EXTRACTION_RECALL_MIN
    judge_ok = judge_acc >= JUDGE_ACCURACY_MIN

    print("Memory eval — Phase 1 bars (design §8)")
    print(_bar("junk precision (zero stored)", junk_ok, f"{junk['pass']}/{junk['total']} (need {JUNK_REQUIRED})"))
    print(_bar("echo suppression", echo_ok, f"{echo['pass']}/{echo['total']} (need {ECHO_REQUIRED})"))
    print(_bar("extraction recall", recall_ok, f"{legit['pass']}/{legit['total']} (need >= {EXTRACTION_RECALL_MIN})"))
    print(_bar("judge action accuracy", judge_ok, f"{judge['pass']}/{judge['total']} = {judge_acc:.0%} (need >= {JUDGE_ACCURACY_MIN:.0%})"))

    # Phase-2 retrieval gate: measure lexical-only vs hybrid in one run and require
    # the hybrid to (a) clear the absolute floor and (b) not regress vs lexical.
    lex, hyb, embeddings_on = _retrieval_lexical_vs_hybrid(golden)
    ret_ok = (
        hyb["hit4"] >= RETRIEVAL_HIT4_MIN and hyb["hit4"] > lex["hit4"]
        if embeddings_on
        else lex["hit4"] >= RETRIEVAL_LEXICAL_MIN
    )
    if embeddings_on:
        detail = (
            f"hybrid {hyb['hit4']}/{hyb['total']} vs lexical {lex['hit4']}/{lex['total']} "
            f"(need >= {RETRIEVAL_HIT4_MIN} and > lexical); "
            f"hit@1 {hyb['hit1']} vs {lex['hit1']}"
        )
    else:
        detail = (
            f"lexical-only {lex['hit4']}/{lex['total']} (need >= {RETRIEVAL_LEXICAL_MIN}); "
            "embeddings unavailable — install model2vec to measure the hybrid"
        )
    print(_bar("retrieval hit@4", ret_ok, detail))

    for key in ("junk", "legit", "echo", "judge"):
        for fail in results[key]["fails"]:
            print(f"    - {key}: {fail}")
    for fail in (hyb if embeddings_on else lex)["fails"]:
        print(f"    - retrieval: {fail}")

    passed = junk_ok and echo_ok and recall_ok and judge_ok and ret_ok
    print(f"\nRESULT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
