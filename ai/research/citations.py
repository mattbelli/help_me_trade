"""Attach report section citations from findings → source ids (not LLM guesses)."""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _claim_supports_section(claim: str, section_body: str) -> bool:
    claim = claim.strip()
    body = section_body.strip()
    if not claim or not body:
        return False
    lowered_claim = claim.lower()
    lowered_body = body.lower()
    if len(lowered_claim) >= 12 and lowered_claim in lowered_body:
        return True
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return False
    body_tokens = _tokens(body)
    overlap = len(claim_tokens & body_tokens)
    return overlap >= max(2, len(claim_tokens) // 3)


def enrich_report_citations(
    report: dict[str, Any],
    findings: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fill section citations from finding source_ids; build programmatic source map."""
    source_map = {
        str(s["id"]): s for s in sources if s.get("id") is not None
    }
    sections = report.get("sections") or []
    if not isinstance(sections, list):
        return report

    for section in sections:
        if not isinstance(section, dict):
            continue
        body = str(section.get("body", ""))
        cites: set[str] = set()
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            source_id = finding.get("source_id")
            if not source_id or str(source_id) not in source_map:
                continue
            claim = str(finding.get("claim", ""))
            if _claim_supports_section(claim, body):
                cites.add(str(source_id))
        if cites:
            section["citations"] = sorted(cites)
        elif not section.get("citations"):
            section["citations"] = []

    unattributed: list[dict[str, Any]] = []
    cited: set[str] = set()
    for section in sections:
        if isinstance(section, dict):
            for cid in section.get("citations") or []:
                cited.add(str(cid))

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        sid = finding.get("source_id")
        if not sid or str(sid) in cited:
            continue
        src = source_map.get(str(sid))
        unattributed.append(
            {
                "source_id": str(sid),
                "claim": str(finding.get("claim", ""))[:300],
                "title": (src or {}).get("title"),
                "url": (src or {}).get("url"),
            }
        )

    report["citation_map"] = [
        {
            "source_id": str(s["id"]),
            "title": s.get("title"),
            "url": s.get("url"),
            "type": s.get("type"),
        }
        for s in sources
        if s.get("id") is not None
    ]
    if unattributed:
        report["unattributed_findings"] = unattributed[:12]
    return report
