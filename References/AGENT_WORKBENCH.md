# Agent Workbench — herdr, worktrees, and how to launch a coder (2026-08-02)

*CURRENT. Founder direction 2026-08-02: **herdr is the workspace manager, exclusively**, the orchestrator
drives it from the CLI, and every coder runs as a **visible agent in a pane** so the founder can watch,
interrupt, and correct it. This doc is the mechanics; `CLAUDE.md` rule 1 is the routing law.*

---

## 0. The two rules everything else serves

1. **Visible, always.** A coder the founder can't see is a coder he can't stop. Headless dispatch is not
   the default and needs a stated reason.
2. **★ ONE WRITER PER WORKING TREE.** This is the whole invariant — everything about worktrees below is
   just the cheapest way to guarantee it. Two agents editing one checkout is the failure mode: they
   overwrite each other's files, stage each other's half-finished work, and produce a diff nobody can
   review.
3. **Never build on `main`.** The checkout at `/home/mbelli/dev/help_me_trade` sits on `main` **and is the
   running deployed backend**. Builds happen somewhere else or they happen on production.

## 1. The bench (who codes what)

| coder | id | use for |
|---|---|---|
| **Grok 4.5 High** | CLI `cursor-grok-4.5-high` · SDK `grok-4.5` | default for real coding work |
| **Opus 5** | CLI `claude-opus-5-thinking-high` · SDK `claude-opus-5` | load-bearing / subtle work, safety-adjacent |
| **Sonnet** | CLI `claude-sonnet-5` · SDK `claude-sonnet-5` | small mechanical tasks, sweeps, boilerplate |
| *GPT-5.6 via codex CLI* | — | **not available** — no Codex sub yet. Do not route here |

**Composer 2.5 is retired as a coder.** The bridge still accepts it; we don't use it. Writing slop 10x
faster loses to well-thought-out slow development every time.

⚠️ **Two model namespaces — never copy an id across them.** The `cursor-agent` CLI wants the tiered id
(`cursor-grok-4.5-high`); the SDK behind `tools/composer-bridge/dispatch.mjs` wants the base id
(`grok-4.5`) with the tier passed separately as `--effort high`. Wrong namespace fails fast with
*"Cannot use this model"* plus the valid list.

## 2. Worktrees — how many, and when

**Branches and worktrees answer different questions.** A branch separates *lines of history*; a worktree
separates *concurrent workspaces*. You need as many branches as you have separate pieces of work, and as
many worktrees as you have **hands writing at the same time**. Those two numbers are usually different.

The professional analogy: on a normal team each developer has their own checkout on their own machine.
Worktrees are how you give each *agent* that same thing, without cloning the repo again.

### The sizing rule — count concurrent writers, NOT tasks

| situation | what to do |
|---|---|
| one agent working | **no new worktree.** Reuse an existing one, new branch inside it |
| a second agent starts while the first is still running | second worktree |
| founder + 2–3 agents (the normal case here) | a small **pool of 2–3 long-lived worktrees**, reused |
| sequential tasks, nothing else running | same worktree, `git switch -c <next-task>` and carry on |

**Do not create a worktree per task.** That is how you end up with a dozen stale checkouts, two of which
turn out to have uncommitted work in them. Create one when there is genuinely another writer.

**Reuse instead of recreate** — when a task lands, return the worktree to the pool:
```bash
cd /home/mbelli/worktrees/help_me_trade/<wt>
git fetch origin
git switch -c <next-task> origin/main    # next job, same folder, fresh off main
git branch -d <finished-task>            # drop the merged branch
```

⚠️ **Do NOT `git switch main` in a worktree here.** It fails —
*"fatal: 'main' is already used by worktree at /home/mbelli/dev/help_me_trade"* — because `main` is
permanently checked out in the live dev tree and no two worktrees may hold the same branch. Branch
**off `origin/main`** (the fetched remote-tracking ref) instead; you never need `main` checked out
locally to start work from it. Same reason `git branch -d` refuses a branch that a worktree still
holds: switch off it first.

### Layout

```
/home/mbelli/worktrees/help_me_trade/<name>      branch <name>, based on origin/main
```

**A worktree is a folder with a checkout in it.** One worktree = one branch at a time, and no two
worktrees can have the same branch checked out. They are *not* children of a branch — there is no
container branch, and we do not use an integration/`develop` branch either. `main` + short-lived task
branches + PR is the flow.

```bash
herdr worktree create --cwd /home/mbelli/dev/help_me_trade \
  --branch <name> --base origin/main \
  --path /home/mbelli/worktrees/help_me_trade/<name> \
  --label <name> --no-focus --json
```

Creating it through herdr (not bare `git worktree add`) makes it a real **workspace** with a pane the
founder can open.

### Before assigning ANY agent: check the tree is free

```bash
herdr workspace list      # agent_status: working | idle — 'working' means occupied, pick another tree
herdr worktree list       # what exists, and which branch each holds
git -C <wt> status --short   # uncommitted work = someone was here; do not stomp it
```

This is the check that prevents the founder's stated failure mode — two agents on one worktree getting in
each other's way. The orchestrator owns it: **never point a coder at a tree whose workspace shows
`working`, and never at one with a dirty status you did not create.**

`herdr worktree remove` cleans up. Prune when the pool grows past what you're actually running.

⚠️ **Worktrees do NOT inherit gitignored files.** A fresh worktree has **no `data/` and no `.env`**. Any
brief whose verify step touches the DB must give the absolute path
`/home/mbelli/dev/help_me_trade/data/trading.db` (2.8 GB) *and* say "copy it first, never open the live
file." Same for anything needing API keys.

**★ Worktrees retire the old "one tree-writer at a time" rule — but only in the sense that made it a
constraint.** The rule existed because parallel coders collided in a single working tree. With one tree
per concurrent writer, parallel dispatch is safe. The invariant itself never went away: it is still
exactly one writer per tree — we just stopped being limited to one tree.
(`commit-hygiene-parallel-agents` still applies *within* a tree: stage by explicit path, verify
`--cached`, never `git add -u/-A`.)

## 3. Launching a coder

```bash
# 1. find the pane (a fresh worktree workspace has exactly one, at a shell prompt)
herdr pane list --workspace <wsID>

# 2. start the agent in it
herdr agent start <name> --kind cursor --pane <wsID>:p1 --timeout 120000 \
  -- --model cursor-grok-4.5-high --force

# 3. send the brief (TEXT is POSITIONAL — there is no --text flag)
herdr agent prompt <name> '<the brief + fences>'
herdr pane send-keys <wsID>:p1 enter        # multiline pastes do NOT auto-submit

# 4. watch / drive
herdr agent get <name>          # agent_status: idle | working | blocked
herdr agent read <name>         # transcript
herdr agent wait <name> --until idle --until blocked --timeout <ms>
herdr agent attach <name>       # take it over by hand
```

**Three gotchas that silently waste a dispatch:**
- **Workspace-trust menu.** In a *fresh* directory `cursor-agent` blocks on `⚠ Workspace Trust Required`
  before reading anything — even with `--force`. Answer it (`herdr pane send-keys <pane> a`) and re-send
  the prompt; the menu swallows whatever was submitted while it was up.
- **Multiline prompts paste without submitting.** Follow every `herdr agent prompt` with `send-keys enter`.
- **`herdr agent prompt <TARGET> <TEXT>`** takes text positionally. `--text` errors out as an unknown option.

## 4. What every dispatch prompt must carry

Beyond the brief itself:

- **Scope fences.** Commit on `<task>` only; **do not merge or push to `main`; do not deploy**; do not
  touch `/home/mbelli/dev/help_me_trade` (live backend) or any other worktree.
- **Staging, if the brief is staged.** Name the stage, and say *stop and report* at its end. Coders
  reliably run ahead into later stages — an unfenced Grok run on the dossier brief opened by redesigning
  `ANGLE_ORDER` for a stage-3 angle.
- **Test invocation.** Name the exact test modules. A bare `pytest` here live-fires outbound HTTPS and
  hangs (see `pytest-piped-exit-codes`, `composer-full-suite-hangs`).
- **"Report what you measured, not what you expected."** A defect that doesn't reproduce is a good
  outcome; a defect confirmed by assumption is how the wildcard fiction got recorded as fact.

## 5. Orchestrator's own git posture

The founder is **learning git, not delegating it away** (CLAUDE.md rule 5). Run the commands, then say
what you ran and why — briefly, in the flow of the answer. Correct the mental model when it's off, not
just the command.

Standing loop for a task: create worktree → dispatch coder → review the real `git diff` → run
`ruff check .` + the named tests → BUILD_LOG row + HANDOFF + `board.json` in the same commit → merge to
`main` → `herdr worktree remove` + delete the branch. Don't leave worktrees or branches lying around;
stale ones accumulate fast and two of them will always turn out to have uncommitted work in them.

## 6. Related

`References/SERVER_SETUP.md` (Node/env for the bridge), `References/STARTUP.md`,
`tools/composer-bridge/README.md` (the headless fallback path — kept, not the default).
