"""Nightly brief routing: which model and effort runs each specialist, and how an arm runs.

The ONE place to change routing. Production memos (Opus at the effort below) feed
the judge; shadow arms run the same prompt on another model/effort and are only
graded, never shown. Every arm's final message is its memo, ending in a fenced
```json block of forward calls; code writes the files, so no session needs Write.

An Opus arm is a headless `claude` session on the founder's subscription. The
DeepSeek arm is the in-process tool loop over the DeepSeek book's read-only
analyst toolset, launched as `python -m research.brief.arms <spec> <date>` so it
runs in parallel with the Claude sessions.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from db.base import ROOT

SPECIALISTS = {"flows": "high", "macro": "xhigh", "stocks": "high", "sectors": "medium"}  # production Opus effort
JUDGE_EFFORT = "max"
GRADER_EFFORT = "high"
SHADOW_ARMS = ("deepseek", "opus_lower")  # opus_lower = one effort step down; togglable

PRODUCTION_ARM = "opus"
OPUS_MODEL = "claude-opus-5-5"
DEEPSEEK_MODEL = "deepseek-v4-flash"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SESSION_TIMEOUT_S = 30 * 60
DEEPSEEK_MAX_ROUNDS = 16
DEEPSEEK_MAX_TOKENS = 32_000
WEB_CALL_CAP = 5

PROMPTS_DIR = ROOT / "research" / "brief_prompts"
_PERCEPTION = "Bash(uv run python tools/perception_query.py:*)"
SPECIALIST_TOOLS = (_PERCEPTION, "Bash(bash tools/fc.sh:*)", "Read", "WebSearch", "WebFetch")
JUDGE_TOOLS = (_PERCEPTION, "Read")
GRADER_TOOLS = ("Read",)
_WEB_TOOLS = frozenset({"WebSearch", "WebFetch", "web_search", "fetch_url"})


def lower_effort(effort: str) -> str:
    i = EFFORTS.index(effort)
    return EFFORTS[max(0, i - 1)]


def arms_for(spec: str, *, shadow: bool = True) -> list[tuple[str, str | None]]:
    """[(arm, opus effort or None)] — production first."""
    effort = SPECIALISTS[spec]
    arms: list[tuple[str, str | None]] = [(PRODUCTION_ARM, effort)]
    if shadow:
        for arm in SHADOW_ARMS:
            arms.append((arm, lower_effort(effort) if arm == "opus_lower" else None))
    return arms


def render_prompt(name: str, **values: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"<<{key.upper()}>>", value)
    return text


def claude_cmd(prompt: str, effort: str, tools: tuple[str, ...]) -> list[str]:
    # --allowedTools is variadic, so it goes last (tools/book_session.sh does the same).
    return ["claude", "--model", OPUS_MODEL, "--effort", effort, "-p", prompt,
            "--output-format", "stream-json", "--verbose", "--allowedTools", *tools]


def _spawn(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    """The single subprocess seam (tests replace it; they never reach a real CLI)."""
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return -9, out, f"timeout after {timeout:.0f}s"
    return p.returncode, p.stdout, p.stderr


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def parse_stream(stdout: str) -> dict[str, Any]:
    """A `claude --output-format stream-json` transcript -> final text, usage, tool results."""
    result: dict[str, Any] | None = None
    tool_texts: list[str] = []
    tool_uses: dict[str, int] = {}
    web_calls = 0
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result":
            result = ev
            continue
        for node in _walk(ev):
            if node.get("type") == "tool_result":
                content = node.get("content")
                if isinstance(content, list):
                    content = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
                tool_texts.append(str(content or ""))
            elif node.get("type") == "tool_use":
                name = str(node.get("name"))
                tool_uses[name] = tool_uses.get(name, 0) + 1
                cmd = str((node.get("input") or {}).get("command", ""))
                if name in _WEB_TOOLS or "tools/fc.sh" in cmd:
                    web_calls += 1
    meta = {k: (result or {}).get(k) for k in (
        "subtype", "is_error", "duration_ms", "num_turns", "total_cost_usd", "usage", "modelUsage")}
    return {
        "text": str((result or {}).get("result") or ""),
        "ok": bool(result) and not result.get("is_error") and bool(result.get("result")),
        "usage": meta,
        "tool_texts": tool_texts,
        "tool_uses": tool_uses,
        "web_calls": web_calls,
    }


def run_claude(prompt: str, effort: str, tools: tuple[str, ...], *,
               timeout: float = SESSION_TIMEOUT_S) -> dict[str, Any]:
    t0 = time.monotonic()
    code, out, err = _spawn(claude_cmd(prompt, effort, tools), timeout)
    res = parse_stream(out)
    res["wall_s"] = round(time.monotonic() - t0, 1)
    res["effort"] = effort
    if code != 0 or not res["ok"]:
        res["ok"] = False
        res["error"] = f"exit {code}: {(err or '').strip()[-500:]}" + (
            "" if res["text"] else " (no final result)")
    return res


_JSON_BLOCK = re.compile(r"```json\s*(.*?)```", re.DOTALL)


def split_json_block(text: str) -> tuple[str, Any]:
    """(markdown without its LAST ```json block, the parsed block or None)."""
    blocks = list(_JSON_BLOCK.finditer(text or ""))
    if not blocks:
        return (text or "").strip(), None
    last = blocks[-1]
    md = (text[: last.start()] + text[last.end():]).strip()
    try:
        return md, json.loads(last.group(1))
    except ValueError:
        return md, None


def validate_calls(obj: Any) -> list[dict[str, Any]]:
    """Keep well-formed forward calls (at most 3)."""
    raw = obj.get("calls") if isinstance(obj, dict) else obj
    out = []
    for c in raw if isinstance(raw, list) else []:
        try:
            call = {
                "claim": str(c["claim"]).strip(),
                "kind": str(c["kind"]),
                "subject": str(c["subject"]).strip().upper() if c["kind"] == "close"
                else str(c["subject"]).strip(),
                "op": str(c["op"]),
                "threshold": float(c["threshold"]),
                "horizon_sessions": int(c["horizon_sessions"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        if (call["claim"] and call["subject"] and call["kind"] in ("close", "series")
                and call["op"] in (">", "<") and 1 <= call["horizon_sessions"] <= 20):
            out.append(call)
    return out[:3]


def write_arm_outputs(brief_dir: Path, spec: str, arm: str, text: str,
                      tool_texts: list[str]) -> dict[str, Any]:
    memo, block = split_json_block(text)
    calls = validate_calls(block)
    key = f"{spec}_{arm}"
    (brief_dir / f"memo_{key}.md").write_text(memo + "\n", encoding="utf-8")
    (brief_dir / f"calls_{key}.json").write_text(json.dumps(calls, indent=1), encoding="utf-8")
    (brief_dir / f"tools_{key}.txt").write_text("\n\n".join(tool_texts), encoding="utf-8")
    return {"calls": len(calls), "memo_chars": len(memo)}


def specialist_prompt(spec: str, et_date: str, next_session: str, brief_dir: Path) -> str:
    return render_prompt(spec, date=et_date, next_session=next_session, brief_dir=str(brief_dir))


# --- the DeepSeek arm (runs in its own process) ---------------------------------------------

_DEEPSEEK_PREAMBLE = (
    "Tool mapping for this session: you have no shell and no file reader. Tonight's "
    "board.md and facts.md are pasted in the user message. Where the instructions say "
    "`perception_query.py context X` use the `get_context` tool; `perception_query.py query R "
    "--filter k=v` is the `query` tool (resource=R, filters={k: v}); `capabilities` is "
    "`list_capabilities`; WebSearch/WebFetch are `web_search`/`fetch_url` (at most "
    f"{WEB_CALL_CAP} of those in total). Your final answer is the memo.\n\n"
)


def run_deepseek_arm(spec: str, et_date: str, next_session: str, brief_dir: Path,
                     *, llm: Any = None) -> dict[str, Any]:
    import asyncio

    from ai.llm.base import LLMMessage
    from ai.llm.factory import get_llm_for_provider
    from ai.llm.mock import MockLLM
    from ai.llm.tool_loop import run_tool_loop
    from research.book_tools import analyst_execute, analyst_tool_specs

    t0 = time.monotonic()
    llm = llm if llm is not None else get_llm_for_provider("deepseek", model=DEEPSEEK_MODEL)
    if isinstance(llm, MockLLM):
        return {"ok": False, "error": "deepseek unconfigured"}
    board_md = (brief_dir / "board.md").read_text(encoding="utf-8")
    facts_path = brief_dir / "facts.md"
    facts_md = facts_path.read_text(encoding="utf-8") if facts_path.exists() else "(no fact digests)"
    tool_texts: list[str] = []
    web = {"n": 0}

    def execute(call: Any) -> str:
        if call.name in _WEB_TOOLS:
            if web["n"] >= WEB_CALL_CAP:
                return json.dumps({"error": f"web budget spent ({WEB_CALL_CAP} calls)"})
            web["n"] += 1
        out = asyncio.run(analyst_execute(call.name, dict(call.arguments or {})))
        text = out if isinstance(out, str) else json.dumps(out, default=str)
        tool_texts.append(text)
        return text

    messages = [
        LLMMessage(role="system", content=_DEEPSEEK_PREAMBLE
                   + specialist_prompt(spec, et_date, next_session, brief_dir)),
        LLMMessage(role="user", content=f"{board_md}\n\n{facts_md}"),
    ]
    result = run_tool_loop(llm, messages, analyst_tool_specs(), execute,
                           max_iterations=DEEPSEEK_MAX_ROUNDS, max_tokens=DEEPSEEK_MAX_TOKENS)
    out = write_arm_outputs(brief_dir, spec, "deepseek", result.text, tool_texts)
    return {"ok": bool(result.text.strip()), "iterations": result.iterations,
            "tool_calls": len(result.tool_calls), "web_calls": web["n"],
            "model": getattr(llm, "model", DEEPSEEK_MODEL),
            "inproc_wall_s": round(time.monotonic() - t0, 1), **out}


def deepseek_cmd(spec: str, brief_dir: Path) -> list[str]:
    return [sys.executable, "-m", "research.brief.arms", spec, str(brief_dir)]


def main(argv: list[str]) -> int:
    import logging

    logging.basicConfig(level=logging.WARNING)
    spec, brief_dir = argv[0], Path(argv[1])
    try:
        board = json.loads((brief_dir / "board.json").read_text(encoding="utf-8"))
        meta = run_deepseek_arm(spec, board["et_date"], board["next_session"], brief_dir)
    except Exception as exc:  # noqa: BLE001 — reported to the orchestrator as data
        meta = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(meta, default=str))
    return 0 if meta.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
