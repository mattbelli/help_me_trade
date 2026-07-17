"""Idea Engine Phase 1 — honesty fix + the three diagnosed nightly-scan bugs.

Covers spec §8 items 5-6 (IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md §6):
  - ``_la_content_state`` forwards the calibration verdict (unproven flag / no bare
    number) instead of stripping the qualifier off the win%.
  - the nightly banner counts ``result["report"]["ideas"]`` by origin (not the
    empty ``result["ideas"]``).
  - ``artifact_exists_by_topic`` is an EXACT match (no prefix/LIKE leakage).
  - the conviction pass retries once on truncation/empty content and counts the
    ok/parse_fail outcome.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ai.research.setup_scan import _run_conviction_pass
from api.main import _count_scan_ideas
from db.ai_tables import artifact_exists_by_topic, save_research_artifact
from signals.push import _la_content_state

# ---------------------------------------------------------------------------
# 5. Honesty — _la_content_state forwards the calibration verdict
# ---------------------------------------------------------------------------


def _proposal(score: dict) -> dict:
    return {
        "id": "p1",
        "proposal": {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 2,
            "order_type": "market",
        },
        "score": score,
    }


def test_la_content_state_unproven_when_insufficient():
    out = _la_content_state(
        _proposal(
            {
                "win_prob": 0.62,
                "ev_pct": 1.5,
                "ci_low": 0.40,
                "n_samples": 5,
                "sufficient": False,
            }
        ),
        status="pending",
    )
    assert out["calibration"] == "unproven"
    assert "winProb" not in out
    assert "evPct" not in out
    assert "ciLow" not in out


def test_la_content_state_unproven_when_win_prob_none():
    out = _la_content_state(
        _proposal({"win_prob": None, "ev_pct": 1.5, "sufficient": True}),
        status="pending",
    )
    assert out["calibration"] == "unproven"
    assert "winProb" not in out
    assert "evPct" not in out


def test_la_content_state_valid_score_travels_with_qualifier():
    out = _la_content_state(
        _proposal(
            {
                "win_prob": 0.62,
                "ev_pct": 1.5,
                "ci_low": 0.55,
                "n_samples": 40,
                "sufficient": True,
            }
        ),
        status="pending",
    )
    # The number and its inseparable qualifier travel together...
    assert out["winProb"] == 0.62
    assert out["ciLow"] == 0.55
    assert out["nSamples"] == 40
    assert out["evPct"] == 1.5
    # ...and there is no "unproven" flag on a sufficient score.
    assert "calibration" not in out


# ---------------------------------------------------------------------------
# 6a. Banner — counts result["report"]["ideas"] by origin
# ---------------------------------------------------------------------------


def test_count_scan_ideas_reads_report_ideas_by_origin():
    result = {
        "report": {
            "ideas": [
                {"origin": "structure", "symbol": "AAPL"},
                {"origin": "structure", "symbol": "MSFT"},
                {"origin": "llm_conviction", "symbol": "NVDA"},
            ]
        }
    }
    assert _count_scan_ideas(result) == (2, 1)


def test_count_scan_ideas_ignores_top_level_ideas():
    # The banner bug: ideas at result["ideas"] must NOT be counted — only report.ideas.
    result = {
        "ideas": [{"origin": "structure"}, {"origin": "structure"}],
        "report": {"ideas": []},
    }
    assert _count_scan_ideas(result) == (0, 0)


def test_count_scan_ideas_handles_missing_report():
    assert _count_scan_ideas({}) == (0, 0)
    assert _count_scan_ideas(None) == (0, 0)
    assert _count_scan_ideas({"report": {"ideas": "not a list"}}) == (0, 0)


# ---------------------------------------------------------------------------
# 6b. Duplicate-run guard — artifact_exists_by_topic is an EXACT match
# ---------------------------------------------------------------------------


def test_artifact_exists_by_topic_exact_match(tmp_path: Path):
    db = tmp_path / "ai.db"
    topic = "Setup scan (nightly) — 2026-07-17"
    save_research_artifact(topic=topic, markdown="x", db_path=db)

    assert artifact_exists_by_topic(topic, db_path=db) is True
    # prefix, suffix, substring and LIKE wildcards must never match
    assert artifact_exists_by_topic("Setup scan (nightly)", db_path=db) is False
    assert artifact_exists_by_topic(f"{topic} extra", db_path=db) is False
    assert artifact_exists_by_topic("Setup scan (nightly) — 2026-07-1%", db_path=db) is False
    assert artifact_exists_by_topic("nonexistent", db_path=db) is False


# ---------------------------------------------------------------------------
# 6c. Conviction pass — one retry on truncation, ok/parse_fail counted
# ---------------------------------------------------------------------------


def _no_structure_assessment(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "has_setup": False,
        "no_structure_reason": "no structure: above nearest support",
        "technicals": {
            "content": {
                "latest_close": 1070.0,
                "sma50": 990.0,
                "sma200": 950.0,
                "ret_3d": -1.2,
                "levels": {
                    "support_prices": [1000.08],
                    "resistance_prices": [1095.69],
                    "dist_to_support_pct": 6.5,
                    "dist_to_resistance_pct": 2.4,
                },
                "rvol": {"value": 1.1, "state": "normal"},
            }
        },
    }


def _install_conviction_stubs(monkeypatch, responses):
    """Fake the LLM call to return each item of ``responses`` in turn; record the
    per-call max_tokens and every lane_stats bump. Returns (calls, bumps)."""
    seq = iter(responses)
    calls: list[int] = []
    bumps: list[tuple[str, str]] = []

    def fake_complete(llm, messages, *, max_tokens):
        calls.append(max_tokens)
        return next(seq)

    monkeypatch.setattr(
        "ai.research.setup_scan.complete_for_extraction", fake_complete
    )
    monkeypatch.setattr("ai.research.setup_scan.get_llm", lambda **kw: object())
    monkeypatch.setattr(
        "ai.research.setup_scan._bump_lane",
        lambda lane, counter: bumps.append((lane, counter)),
    )
    return calls, bumps


@pytest.mark.asyncio
async def test_conviction_pass_retries_once_then_ok(monkeypatch):
    effort = type("P", (), {"per_source_content_limit": 4000})()
    # First response is unparseable (truncation), second is valid JSON.
    calls, bumps = _install_conviction_stubs(
        monkeypatch,
        ["<<< truncated non-json response", '{"ideas": []}'],
    )

    validated, rejected, journal = await _run_conviction_pass(
        no_structure_assessments=[_no_structure_assessment("MSFT")],
        last_prices={"MSFT": Decimal("1070")},
        effort_preset=effort,
    )

    assert calls == [6000, 6000]  # raised budget + exactly one retry
    assert ("conviction_pass", "ok") in bumps
    assert ("conviction_pass", "parse_fail") not in bumps
    assert validated == []
    assert rejected == []
    assert journal["note"] == "no conviction ideas this scan"


@pytest.mark.asyncio
async def test_conviction_pass_parse_fail_after_retry_is_counted(monkeypatch):
    effort = type("P", (), {"per_source_content_limit": 4000})()
    # Both attempts unparseable → one retry, then parse_fail counted, fail-soft.
    calls, bumps = _install_conviction_stubs(
        monkeypatch,
        ["<<< garbage one", "<<< garbage two"],
    )

    validated, rejected, journal = await _run_conviction_pass(
        no_structure_assessments=[_no_structure_assessment("MSFT")],
        last_prices={"MSFT": Decimal("1070")},
        effort_preset=effort,
    )

    assert len(calls) == 2  # one retry, no more
    assert ("conviction_pass", "parse_fail") in bumps
    assert ("conviction_pass", "ok") not in bumps
    assert validated == []
    assert rejected == []
    assert journal["note"] == "conviction pass returned invalid JSON"
