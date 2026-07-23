"""Read-only analyst toolset for the LLM book drivers (book-analyst-charter).

A curated ALLOWLIST over the chat tool registry — our data (perception reads)
plus outside data (web search). Deny-by-default twice over: only listed names
get specs, and the executor re-checks the name at call time so a model can
never reach a tool outside the list (the mutating chat tools — orders, rules,
watchlist edits, memories, start_research — are structurally out).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ai.chat.tool_specs import chat_tool_specs
from ai.llm.base import ToolSpec

logger = logging.getLogger(__name__)

# start_research stays banned no matter how useful it looks — a driver that can
# enqueue deep research can self-escalate into detached fan-out
# (agent-research-fanout-hazard).
ANALYST_TOOLS = frozenset(
    {
        "get_context",  # everything the app knows about one symbol
        "list_capabilities",  # the perception manifest
        "query",  # app data across resources (watchlists, regime, flow_report…)
        "get_earnings_dates",
        "web_search",  # outside data via SearXNG
    }
)

# One tool result can be huge (get_context on a busy symbol); cap what flows
# back into the model's context so a single call can't blow the turn.
_RESULT_CHAR_CAP = 20_000


def analyst_tool_specs() -> list[ToolSpec]:
    return chat_tool_specs(enabled=set(ANALYST_TOOLS))


async def analyst_execute(name: str, args: dict[str, Any]) -> Any:
    if name not in ANALYST_TOOLS:
        logger.warning("book analyst blocked tool call %r", name)
        return {"error": f"tool {name!r} is not available to book analysts"}
    from ai.chat.tools import execute_tool

    output = await execute_tool(name, args)
    rendered = output if isinstance(output, str) else json.dumps(output, default=str)
    if len(rendered) > _RESULT_CHAR_CAP:
        return rendered[:_RESULT_CHAR_CAP] + f"\n…(truncated at {_RESULT_CHAR_CAP} chars)"
    return output
