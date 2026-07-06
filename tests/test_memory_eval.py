"""Smoke test for the memory eval harness (tools/memory_eval.py).

Proves the harness loads the golden set and runs end-to-end offline (deterministic
MockLLM). Accuracy bars are a *live-LLM* concern; here we assert the deterministic
invariants: the mock extractor fabricates no junk (junk + echo fully suppressed) and
the shapes/totals are correct.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "memory_eval",
    Path(__file__).resolve().parents[1] / "tools" / "memory_eval.py",
)
memory_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(memory_eval)  # type: ignore[union-attr]


def test_golden_set_has_expected_shape() -> None:
    golden = memory_eval.load_golden()
    assert len(golden["extraction"]["junk"]) == 12
    assert len(golden["extraction"]["legit"]) == 10
    assert len(golden["extraction"]["echo"]) == 6
    assert len(golden["judge"]) == 8
    assert len(golden["retrieval"]) == 15


def test_harness_runs_and_suppresses_junk_offline() -> None:
    golden = memory_eval.load_golden()
    results = memory_eval.run_eval(golden)
    assert results["junk"]["total"] == 12
    assert results["legit"]["total"] == 10
    assert results["echo"]["total"] == 6
    assert results["judge"]["total"] == 8
    # The deterministic mock never fabricates a memory from junk / echoed turns.
    assert results["junk"]["pass"] == 12
    assert results["echo"]["pass"] == 6
