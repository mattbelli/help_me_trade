#!/usr/bin/env bash
# Nightly LLM book analyst session (fable|panel) — book-analyst-charter.
# Runs a headless Claude Code session on the pre-registered prompt
# (research/book_prompts/<book>.md); the session's last act is
# tools/book_session_submit.py which registers lanes + delivers the report.
# The panel session may spawn EXACTLY its four named Opus seats (Agent tool);
# the fable session runs solo.
set -euo pipefail
BOOK="${1:?usage: book_session.sh fable|panel}"
cd "$(dirname "$0")/.."
export PATH="/home/mbelli/.local/bin:$PATH"
mkdir -p data/book_sessions/work

TOOLS=(
  "Bash(uv run python tools/book_context_pack.py:*)"
  "Bash(uv run python tools/book_session_submit.py:*)"
  "Read"
  "Write"
  "WebSearch"
  "WebFetch"
)
if [ "$BOOK" = "panel" ]; then
  TOOLS+=("Agent")
fi

exec claude --model fable -p "$(cat "research/book_prompts/${BOOK}.md")" \
  --allowedTools "${TOOLS[@]}"
