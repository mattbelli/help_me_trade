"""Structured research report + Odysseus-style HTML renderer."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from typing import Any

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm


def _parse_report_json(raw: str, *, topic: str, symbol: str | None) -> dict[str, Any]:
    text = raw.strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return _normalize_report(parsed, topic=topic, symbol=symbol)
    except json.JSONDecodeError:
        pass
    return _fallback_report(topic=topic, symbol=symbol, body=text)


def _normalize_report(
    data: dict[str, Any], *, topic: str, symbol: str | None
) -> dict[str, Any]:
    sections_in = data.get("sections", [])
    sections: list[dict[str, Any]] = []
    if isinstance(sections_in, list):
        for sec in sections_in:
            if isinstance(sec, dict):
                sections.append(
                    {
                        "heading": str(sec.get("heading", "Section")),
                        "body": str(sec.get("body", "")),
                        "citations": sec.get("citations", []),
                    }
                )
    metrics_in = data.get("metrics", [])
    metrics: list[dict[str, str]] = []
    if isinstance(metrics_in, list):
        for m in metrics_in:
            if isinstance(m, dict):
                metrics.append(
                    {
                        "label": str(m.get("label", "")),
                        "value": str(m.get("value", "")),
                    }
                )
    sources_in = data.get("sources", [])
    sources: list[dict[str, Any]] = []
    if isinstance(sources_in, list):
        for s in sources_in:
            if isinstance(s, dict):
                sources.append(s)
    return {
        "title": str(data.get("title") or topic),
        "symbol": symbol,
        "summary": str(data.get("summary", "")),
        "metrics": metrics,
        "sections": sections
        or [
            {
                "heading": "Analysis",
                "body": str(data.get("summary", "")),
                "citations": [],
            }
        ],
        "risks": [str(r) for r in data.get("risks", []) if r],
        "next_steps": [str(n) for n in data.get("next_steps", []) if n],
        "sources": sources,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _fallback_report(*, topic: str, symbol: str | None, body: str) -> dict[str, Any]:
    return {
        "title": topic,
        "symbol": symbol,
        "summary": body[:500],
        "metrics": [],
        "sections": [{"heading": "Report", "body": body, "citations": []}],
        "risks": [],
        "next_steps": [],
        "sources": [],
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_report_from_findings(
    *,
    topic: str,
    symbol: str | None,
    sources: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    llm = get_llm()
    source_catalog = [
        {
            "id": s.get("id"),
            "type": s.get("type"),
            "title": s.get("title"),
            "snippet": s.get("snippet"),
        }
        for s in sources
    ]
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are a trading research editor. Return JSON only with keys: "
                "title, summary, metrics (array of {label,value}), sections "
                "(array of {heading, body, citations}), risks (array), "
                "next_steps (array), sources (array of {id,title,url,type}). "
                "Citations in sections must reference source ids. Be factual — "
                "only use provided findings."
            ),
        ),
        LLMMessage(
            role="user",
            content=json.dumps(
                {
                    "topic": topic,
                    "symbol": symbol,
                    "findings": findings,
                    "source_catalog": source_catalog,
                },
                indent=2,
                default=str,
            )[:12000],
        ),
    ]
    raw = llm.complete(messages, max_tokens=2000)
    report = _parse_report_json(raw, topic=topic, symbol=symbol)
    if not report.get("sources"):
        report["sources"] = [
            {
                "id": s.get("id"),
                "title": s.get("title"),
                "url": s.get("url"),
                "type": s.get("type"),
            }
            for s in sources
        ]
    return report


def report_to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report.get('title', 'Research')}", ""]
    if report.get("summary"):
        lines.extend([report["summary"], ""])
    metrics = report.get("metrics") or []
    if metrics:
        lines.append("## Key metrics")
        for m in metrics:
            lines.append(f"- **{m.get('label')}**: {m.get('value')}")
        lines.append("")
    for sec in report.get("sections") or []:
        lines.append(f"## {sec.get('heading', 'Section')}")
        lines.append(str(sec.get("body", "")))
        lines.append("")
    risks = report.get("risks") or []
    if risks:
        lines.append("## Risks")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")
    steps = report.get("next_steps") or []
    if steps:
        lines.append("## Next steps")
        for s in steps:
            lines.append(f"- {s}")
        lines.append("")
    sources = report.get("sources") or []
    if sources:
        lines.append("## Sources")
        for s in sources:
            title = s.get("title", "Source")
            url = s.get("url")
            lines.append(f"- [{title}]({url})" if url else f"- {title}")
    return "\n".join(lines).strip()


def render_report_html(report: dict[str, Any]) -> str:
    title = html.escape(str(report.get("title", "Research Report")))
    summary = html.escape(str(report.get("summary", "")))
    symbol = report.get("symbol")
    sym_badge = (
        f'<span class="badge">{html.escape(str(symbol))}</span>' if symbol else ""
    )
    generated = html.escape(str(report.get("generated_at", ""))[:19])

    metrics_html = ""
    for m in report.get("metrics") or []:
        metrics_html += f"""
        <div class="metric-card">
          <div class="metric-label">{html.escape(str(m.get('label', '')))}</div>
          <div class="metric-value">{html.escape(str(m.get('value', '')))}</div>
        </div>"""

    sections_html = ""
    source_map = {s.get("id"): s for s in report.get("sources") or []}
    for sec in report.get("sections") or []:
        body = html.escape(str(sec.get("body", "")))
        body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
        body = body.replace("\n", "<br/>")
        cites = sec.get("citations") or []
        cite_links = " ".join(
            f'<a class="cite" href="#{html.escape(str(c))}">[{html.escape(str(c))}]</a>'
            for c in cites
            if c in source_map
        )
        sections_html += f"""
        <section class="report-section">
          <h2>{html.escape(str(sec.get('heading', 'Section')))}</h2>
          <p>{body}</p>
          <div class="citations">{cite_links}</div>
        </section>"""

    risks = report.get("risks") or []
    risks_html = "".join(f"<li>{html.escape(str(r))}</li>" for r in risks)
    steps = report.get("next_steps") or []
    steps_html = "".join(f"<li>{html.escape(str(s))}</li>" for s in steps)

    sources_html = ""
    for s in report.get("sources") or []:
        sid = html.escape(str(s.get("id", "")))
        stitle = html.escape(str(s.get("title", "Source")))
        stype = html.escape(str(s.get("type", "")))
        url = s.get("url")
        link = (
            f'<a href="{html.escape(str(url))}" target="_blank" rel="noopener">{stitle}</a>'
            if url
            else stitle
        )
        sources_html += f"""
        <li id="{sid}">
          <span class="source-type">{stype}</span>
          {link}
        </li>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0f1115; --card: #171a21; --text: #e8eaed; --muted: #9aa0a6;
      --accent: #6ee7b7; --border: #2a2f3a; --hero: #1a2332;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.6;
    }}
    .wrap {{ max-width: 820px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
    .hero {{
      background: linear-gradient(135deg, var(--hero), var(--card));
      border: 1px solid var(--border); border-radius: 16px;
      padding: 2rem; margin-bottom: 2rem;
    }}
    h1 {{ margin: 0 0 0.5rem; font-size: 1.75rem; letter-spacing: -0.02em; }}
    .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem; }}
    .badge {{
      display: inline-block; background: #243044; color: var(--accent);
      padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.75rem;
      margin-left: 0.5rem; vertical-align: middle;
    }}
    .summary {{ font-size: 1.05rem; color: #c9cdd3; }}
    .metrics {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 0.75rem; margin: 1.5rem 0 2rem;
    }}
    .metric-card {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 1rem;
    }}
    .metric-label {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; }}
    .metric-value {{ font-size: 1.25rem; font-weight: 600; margin-top: 0.25rem; }}
    .report-section {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1rem;
    }}
    h2 {{ font-size: 1.1rem; margin: 0 0 0.75rem; color: var(--accent); }}
    .citations {{ margin-top: 0.75rem; }}
    .cite {{
      color: var(--accent); text-decoration: none; font-size: 0.8rem; margin-right: 0.35rem;
    }}
    .dual {{ display: grid; gap: 1rem; grid-template-columns: 1fr 1fr; }}
    @media (max-width: 640px) {{ .dual {{ grid-template-columns: 1fr; }} }}
    .panel {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 1.25rem;
    }}
    ul {{ margin: 0; padding-left: 1.1rem; }}
    li {{ margin-bottom: 0.4rem; }}
    .sources {{ margin-top: 2rem; }}
    .source-type {{
      display: inline-block; font-size: 0.65rem; text-transform: uppercase;
      color: var(--muted); border: 1px solid var(--border); border-radius: 4px;
      padding: 0.1rem 0.35rem; margin-right: 0.5rem;
    }}
    a {{ color: #7eb8ff; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <h1>{title}{sym_badge}</h1>
      <div class="meta">Deep research · {generated}</div>
      <p class="summary">{summary}</p>
    </header>
    <div class="metrics">{metrics_html}</div>
    {sections_html}
    <div class="dual">
      <div class="panel">
        <h2>Risks</h2>
        <ul>{risks_html or "<li>None highlighted</li>"}</ul>
      </div>
      <div class="panel">
        <h2>Next steps</h2>
        <ul>{steps_html or "<li>Monitor and revisit</li>"}</ul>
      </div>
    </div>
    <section class="sources panel">
      <h2>Sources analyzed</h2>
      <ul>{sources_html}</ul>
    </section>
  </div>
</body>
</html>"""
