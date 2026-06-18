# composer-bridge

A thin wrapper around the official [Cursor SDK](https://cursor.com/docs/sdk/typescript)
(`@cursor/sdk`) so the **orchestrator (Claude Code)** can dispatch **Composer 2.5**
coding agents against this repo — the plan → brief → code → review loop, but with
Claude as the brain instead of a Cursor interactive agent.

This solves the limit problem from the old workflow: the expensive orchestration
(planning, review) runs on the flat Anthropic subscription via Claude Code, while
the cheap, high-volume coding runs on Composer 2.5 via this bridge.

## Why this and not `claude-ide-bridge`?

That project (Oolab-labs) is an MCP server that gives Claude *IDE features* (LSP,
debug, git). It does **not** dispatch Composer coders. The official Cursor SDK is
the supported path for running Composer agents programmatically.

## One-time setup

```powershell
# 1. Install the SDK (isolated here — does not touch frontend deps)
cd tools/composer-bridge
npm install

# 2. Get a Cursor API key: https://cursor.com/dashboard/api
#    (Service-account key for a server later: Team settings.)
$env:CURSOR_API_KEY = "your-key-here"     # PowerShell, current session
```

> **Billing note:** SDK Composer runs bill your Cursor account/credits (separate
> from the IDE plan). Confirm you have credits, or the bridge won't relieve the
> limit problem.

## Usage

```powershell
# From the repo root, with $env:CURSOR_API_KEY set:

# Dispatch a written brief (the normal path)
node tools/composer-bridge/dispatch.mjs --brief References/PHASE_X_BRIEF.md

# Inline prompt for a quick one-off
node tools/composer-bridge/dispatch.mjs --prompt "Fix the SW resize handle orientation in workspace-grid"

# Safer mode: route the agent's tool calls through Cursor Auto-review
node tools/composer-bridge/dispatch.mjs --brief References/PHASE_X_BRIEF.md --review
```

Flags: `--brief <file>`, `--prompt <text>`, `--cwd <dir>` (default: repo root),
`--model <id>` (default `composer-2.5`), `--review`, `--no-stream`. Exit code is
`0` on `finished`, `1` otherwise.

## End-state (per IDEA_BANK §7.6 / §7.9)

`local.cwd` runs wherever the repo lives, so this same script runs **server-side**
once the headless core + gateway exist: Claude Code (over SSH) plans, Composer
coders execute on the server, and you reach it all from PC / laptop / phone.
Build local-first now; it ports with no redesign.
