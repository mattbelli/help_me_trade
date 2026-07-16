"""Hermetic tests for setup scan conviction lane."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from ai.research.setup_scan import (
    _append_conviction_section,
    _build_conviction_candidate_summary,
    _run_conviction_pass,
)


def _no_structure_assessment(
    symbol: str,
    *,
    close: float = 1070.0,
    support: float = 1000.08,
    resistance: float = 1095.69,
) -> dict:
    return {
        "symbol": symbol,
        "has_setup": False,
        "no_structure_reason": "no structure: 6.5% above nearest support 1000.08",
        "technicals": {
            "content": {
                "latest_close": close,
                "sma50": 990.0,
                "sma200": 950.0,
                "ret_3d": -1.2,
                "levels": {
                    "support_prices": [support],
                    "resistance_prices": [resistance],
                    "dist_to_support_pct": 6.5,
                    "dist_to_resistance_pct": 2.4,
                },
                "rvol": {"value": 1.1, "state": "normal"},
            }
        },
    }


def test_build_conviction_candidate_summary_from_technicals():
    summary = _build_conviction_candidate_summary(
        "MSFT",
        _no_structure_assessment("MSFT")["technicals"]["content"],
    )
    assert summary["symbol"] == "MSFT"
    assert summary["latest_close"] == 1070.0
    assert summary["close_vs_sma50"] == "above"
    assert summary["support_prices"] == [1000.08]
    assert summary["rvol"] == 1.1
    assert summary["ret_3d"] == -1.2


@pytest.mark.asyncio
async def test_conviction_pass_parses_validates_and_tags_origin():
    llm_response = {
        "ideas": [
            {
                "symbol": "MSFT",
                "entry_zone": {"low": "1055", "high": "1065"},
                "stop": "1040",
                "targets": ["1090"],
                "thesis": "Pullback toward 1000.08 support with close 1070 holding above SMA50.",
                "confidence": "medium",
                "confidence_reason": "Defined levels from scan data.",
                "key_risks": ["Macro risk"],
            }
        ]
    }
    last_prices = {"MSFT": Decimal("1070")}
    effort = type("P", (), {"per_source_content_limit": 4000})()

    with (
        patch(
            "ai.research.setup_scan.complete_for_extraction",
            return_value='{"ideas":[]}',
        ),
        patch(
            "ai.research.setup_scan.parse_json_object",
            return_value=llm_response,
        ),
        patch("ai.research.setup_scan.get_llm"),
    ):
        validated, rejected, journal = await _run_conviction_pass(
            no_structure_assessments=[_no_structure_assessment("MSFT")],
            last_prices=last_prices,
            effort_preset=effort,
        )

    assert rejected == []
    assert len(validated) == 1
    assert validated[0]["origin"] == "llm_conviction"
    assert validated[0]["symbol"] == "MSFT"
    assert journal["nominated"] == [{"symbol": "MSFT"}]


@pytest.mark.asyncio
async def test_conviction_pass_llm_failure_is_fail_soft():
    effort = type("P", (), {"per_source_content_limit": 4000})()

    with (
        patch(
            "ai.research.setup_scan.complete_for_extraction",
            side_effect=RuntimeError("LLM down"),
        ),
        patch("ai.research.setup_scan.get_llm"),
    ):
        validated, rejected, journal = await _run_conviction_pass(
            no_structure_assessments=[_no_structure_assessment("MSFT")],
            last_prices={"MSFT": Decimal("1070")},
            effort_preset=effort,
        )

    assert validated == []
    assert rejected == []
    assert "error" in str(journal["note"]).lower()


@pytest.mark.asyncio
async def test_conviction_pass_invalid_json_is_fail_soft():
    effort = type("P", (), {"per_source_content_limit": 4000})()

    with (
        patch(
            "ai.research.setup_scan.complete_for_extraction",
            return_value="not json",
        ),
        patch("ai.research.setup_scan.parse_json_object", return_value=None),
        patch("ai.research.setup_scan.get_llm"),
    ):
        validated, rejected, journal = await _run_conviction_pass(
            no_structure_assessments=[_no_structure_assessment("MSFT")],
            last_prices={"MSFT": Decimal("1070")},
            effort_preset=effort,
        )

    assert validated == []
    assert rejected == []
    assert journal["note"] == "conviction pass returned invalid JSON"


@pytest.mark.asyncio
async def test_conviction_pass_no_conviction_journals_cleanly():
    effort = type("P", (), {"per_source_content_limit": 4000})()

    with (
        patch(
            "ai.research.setup_scan.complete_for_extraction",
            return_value='{"ideas":[]}',
        ),
        patch(
            "ai.research.setup_scan.parse_json_object",
            return_value={"ideas": []},
        ),
        patch("ai.research.setup_scan.get_llm"),
    ):
        validated, rejected, journal = await _run_conviction_pass(
            no_structure_assessments=[_no_structure_assessment("MSFT")],
            last_prices={"MSFT": Decimal("1070")},
            effort_preset=effort,
        )

    assert validated == []
    assert rejected == []
    assert journal["note"] == "no conviction ideas this scan"
    assert journal["nominated"] == []


def test_append_conviction_section_inserts_before_no_setup():
    base = "# Scan\n\n**Ideas**\n\n## No-setup candidates\n\n- none\n"
    ideas = [
        {
            "symbol": "MSFT",
            "entry_zone": {"low": "1055", "high": "1065"},
            "stop": "1040",
            "targets": ["1090"],
            "confidence": "medium",
            "thesis": "Test conviction thesis.",
            "confidence_reason": "Grounded in provided levels.",
            "risks": ["Risk one"],
        }
    ]
    out = _append_conviction_section(base, ideas)
    assert "## Conviction ideas (LLM-originated — unproven)" in out
    assert out.index("Conviction ideas") < out.index("No-setup candidates")
