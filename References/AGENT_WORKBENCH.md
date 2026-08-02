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

### Naming: the DIRECTORY is the tree's identity, the BRANCH is the work's identity

```
/home/mbelli/worktrees/help_me_trade/<tree>      holds branch <task>, based on origin/main
```

Trees get **stable, neutral names** (`orch`, `wt-a`, `wt-b`). Branches get **task names**. A tree named
after a task is a tree nobody will ever reuse — it reads as finished the moment the task is, so the next
job gets a new folder and the stale pile grows. Naming a tree `coder-routing-doctrine` (2026-08-02) is
exactly the mistake: the work landed and the folder instantly looked like garbage.

### ⚠️ A herdr *workspace* is for an AGENT to live in — don't make one you won't watch

`herdr worktree create` builds a worktree **and** a herdr workspace with a pane. That pane is the whole
point *when an agent runs there*. When the **orchestrator itself** is the writer, there is nothing to
watch — use plain git and leave the workspace list clean:

```bash
git -C /home/mbelli/dev/help_me_trade worktree add -b <task> \
  /home/mbelli/worktrees/help_me_trade/orch origin/main
```

Rule of thumb: **if you can't name the agent that will occupy it, don't create a workspace.**

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

### Cleaning up

`herdr worktree remove --workspace <wsID>` (not `--path`) removes a workspace-backed tree; plain
`git worktree remove <path>` handles the orchestrator's own. Either way **check first** — a tree with
unpushed commits or a dirty status is someone's unfinished work:

```bash
git -C <wt> log --oneline origin/main..HEAD    # unpushed
git -C <wt> status --short                     # uncommitted
```

Remove a tree when its round lands; keep one only while an agent is actively cycling work through it.
Trees are cheap to make (~1s, they share the `.git` object store), so on-demand beats hoarding — the six
stale `worktree-agent-*` checkouts under `dev/help_me_trade/.claude/worktrees/`, two with uncommitted
work in them, are what hoarding looks like after three days.

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

> **The `herdr` skill is the authority on CLI syntax** — installed at `~/.claude/skills/herdr/SKILL.md`
> (upstream: `github.com/herdrdev/herdr/skills/herdr/SKILL.md`; companion overview at
> `herdr.dev/agent-guide.md`). Load it before driving herdr, and prefer `herdr <group>` help output over
> anything written here. Requires `HERDR_ENV=1`; your own context is in `$HERDR_WORKSPACE_ID` /
> `$HERDR_TAB_ID` / `$HERDR_PANE_ID`. Herdr is **not** tmux — no tmux commands or config syntax.

**Default topology is a sibling pane in the current tab, not a new workspace.** The skill is explicit:
don't create a workspace, tab, worktree, or different cwd unless the user asked for it. *Our standing
exception:* the founder has asked for worktrees, so worktree-backed workspaces are sanctioned **when an
agent will live there** (§2). For a quick agent alongside your own work, just split:

```bash
herdr pane layout --pane "$HERDR_PANE_ID"                                  # wide -> right, tall -> down
herdr pane split --current --direction right --cwd "$PWD" --no-focus
# new pane id = .result.pane.pane_id  (parse it from JSON, never guess)
```

```bash
# 1. find the pane (a fresh worktree workspace has exactly one, at a shell prompt)
herdr pane list --workspace <wsID>

# 2. start the agent in it — native agent args go AFTER `--`
herdr agent start <name> --kind cursor --pane <wsID>:p1 --timeout 120000 \
  -- --model cursor-grok-4.5-high --force

# 3. send the brief (TEXT is POSITIONAL — there is no --text flag)
herdr agent prompt <name> '<the brief + fences>' --wait

# 4. watch / drive
herdr agent get <name>                                      # idle | working | blocked | done | unknown
herdr agent read <name> --source recent-unwrapped --lines 120
herdr agent wait <name>                                     # bare — see the ⚠ below
herdr agent attach <name>                                   # take it over by hand
herdr agent explain <name> --json                           # why detection thinks what it thinks
```

**Gotchas that silently waste a dispatch:**

- ⚠️ **NEVER spell out `--until idle` on a wait.** Bare `--wait` / bare `agent wait` already settles on
  `idle`, `done`, **or** `blocked`. `done` is the same idle state reached after **unseen** background work
  finishes — and **CLI reads do not mark a tab seen**, so an agent you only ever polled from the CLI
  lands in `done`, not `idle`. Measured 2026-08-02: `agent wait --until idle --until blocked` hung ~25
  minutes while the agent sat finished; the bare wait returned instantly. Use `--until` only for a
  genuinely state-specific workflow (e.g. `--until blocked` to catch an approval prompt).
- **Workspace-trust menu.** In a *fresh* directory `cursor-agent` blocks on `⚠ Workspace Trust Required`
  before reading anything — **even with `--force`**. Answer it *first*
  (`herdr pane send-keys <pane> a`), then send the prompt; the menu swallows anything submitted while
  it's up, and that's what leaves text sitting unsent in the composer.
- **`agent prompt` submits text AND Enter atomically** (it honors the pane's bracketed-paste mode), so
  a trailing `send-keys enter` should not be needed. If you do find a prompt parked in the composer —
  which is what the trust modal causes — `herdr pane send-keys <pane> enter` clears it.
- **`herdr agent prompt <TARGET> <TEXT>`** takes text positionally. `--text` errors as an unknown option.
- **Reading a TUI agent.** `cursor-agent` renders on the terminal's **alternate screen**, so rows that
  scroll away never reach herdr's scrollback and raising `--lines` cannot recover them. When a read comes
  back truncated, ask the agent to write its full response to a temp `.md` and reply with only the path,
  then read the file. Fallback only — don't ask for file output in the initial prompt.
- **CLI error contract:** server errors are JSON on **stderr** with exit 1; syntax errors exit 2.

**Safety rules (from the skill, not optional):** `--no-focus` for background work; target `--current`, an
explicit pane ID, or a unique agent name — never another client's focused pane; parse IDs from JSON, never
from sidebar order; **do not close workspaces/tabs/panes you did not create**; and **never**
`herdr server stop` on the live session — it kills every pane process, including the founder's.

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
