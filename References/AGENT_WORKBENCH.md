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

| coder | launch | use for |
|---|---|---|
| **Grok 4.5 High** | cursor-agent: `--kind cursor -- --model cursor-grok-4.5-high` | default for real coding work |
| **Opus 5** | Claude Code: `--kind claude -- --model opus` | load-bearing / subtle work, safety-adjacent |
| **Sonnet** | Claude Code: `--kind claude -- --model sonnet` | small mechanical tasks, sweeps, boilerplate |
| *GPT-5.6 via codex CLI* | — | **not available** — no Codex sub yet. Do not route here |

⚠️ **Claude-family coders run via the Claude Code CLI on the founder's Claude subscription — NEVER
through cursor-agent** (founder correction 2026-08-10: a widget dispatch used cursor's
`claude-opus-5-thinking-high` and billed Cursor credits; cursor *offers* Claude ids, don't take them).
cursor-agent is for non-Claude models only. (Agent sessions on the subscription are fine — the
CLAUDE.md ToS rule forbids subscription/OAuth for the APP's runtime LLM, not for coding agents.)

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

⚠️ **Worktree dev servers: `frontend/node_modules` is a symlink into the main dev tree, and Next 16's
Turbopack refuses to follow it** (*"Symlink node_modules is invalid, it points out of the filesystem
root"*). For gallery screenshots (`tools/ui-shot.mjs` needs a server on 3055) start it with
`npx next dev --webpack -p 3055` from the worktree's `frontend/` — and note `npm run dev -- -p 3055`
doesn't override the port anyway (the script hardcodes `-p 3001`; the flag just appends). Discovered
by the account-column coder, 2026-08-10.

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

⚠️ **Remove the WORKTREE before closing its WORKSPACE — the order is not interchangeable.** Because
`worktree remove` is keyed to `--workspace <id>`, closing the workspace first strands the handle
(`open_workspace_id: null`) and the tree can then only be cleaned with plain `git worktree remove`.
Founder correction 2026-08-02, after I closed first and then went looking for the option that had
"disappeared."

**Remove a tree when the *thread* ends, not when a round lands.** Founder direction 2026-08-02
(*"why don't we just make this dossier so we don't change worktrees for each d5 d6 etc"*): a multi-round
build keeps one tree across all its rounds and recycles the branch (`git fetch origin && git switch -c
<next> origin/main`). It's the **agent** that's disposable, not the tree — see §3's fresh-agent rule.
Trees are cheap to make (~1s, shared `.git` object store), so don't hoard *unrelated* ones: the six
stale `worktree-agent-*` checkouts under `dev/help_me_trade/.claude/worktrees/`, two with uncommitted
work in them, are what hoarding looks like after three days.

⚠️ **`git worktree move` poisons `__pycache__` and produces PHANTOM test failures.** `.pyc` files bake
the absolute source path into `co_filename`, so after a move `linecache` can't resolve it and any test
asserting on a rendered traceback or thread-stack dump fails — deterministically, looking exactly like a
real regression in code you never touched. Measured 2026-08-02: `tests/test_loop_probe.py` failed 3/3 in
the renamed tree with the *old* path still in the dump, and passed immediately after
`find . -name __pycache__ -prune -exec rm -rf {} +`. Clear the cache as the second half of every move.

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
- ⚠️ **`agent prompt` does NOT submit to `cursor-agent` — on ANY prompt. Always follow with `enter`.**
  The skill states that `agent prompt` sends text and Enter atomically, honoring bracketed paste. That is
  the herdr contract; it does **not** survive contact with cursor-agent. **Line count is irrelevant** —
  an earlier version of this note blamed the "multiline composer" and said short prompts were fine; a
  later single-line dispatch parked identically. Measured 5× across 2026-08-02, three distinct modes:
  1. the text parks as `[Pasted text #N +19 lines]`, status stays `idle`;
  2. a **one-line** prompt parks the same way;
  3. **the text never lands at all** — the composer is *empty* and a blind `enter` submits nothing,
     which is the worst case because the agent looks dispatched and simply never runs.
  ```bash
  herdr agent prompt <name> '<brief>'
  herdr agent read <name> --source detection --lines 12   # composer must actually hold something
  herdr pane send-keys <pane> enter
  herdr agent get <name>                   # must read `working` — if still `idle`, it never started
  ```
  ⚠️ **Do not verify by grepping the pane for your brief's literal text** — the composer collapses a
  paste to `[Pasted text #1 +N lines]`, so the grep finds nothing *even on success* and you'll re-send a
  prompt that already landed. Grep for `[Pasted text` too; the only trustworthy signal is `agent_status`
  flipping to `working`. A prompt that silently never ran looks exactly like an agent that finished
  instantly. (Reported upstream-worthy: the skill's atomic claim is wrong for this agent kind.)
- ⚠️ **Arm the wait AS PART OF THE DISPATCH, never as a follow-up thought.** A backgrounded
  `herdr agent wait <name>` is the *only* thing that wakes the orchestrator when an agent finishes —
  with no wait running, a finished agent sits idle indefinitely and the founder is the one who notices.
  Measured failure 2026-08-02: armed for the first dispatch of a round, dropped for the next three;
  founder caught it twice in a row ("is your monitor working?"). Treat `prompt → enter → confirm
  working → background wait` as one indivisible four-step action.
- **`--force` (alias `--yolo`) does NOT disable the denylist** — it means "allow unless *explicitly*
  denied", so yolo + denylist coexist and that combination is the right way to run a dispatched pane
  agent: no per-command approval stalls, dangerous verbs still blocked. The list lives in
  `~/.cursor/cli-config.json` (`permissions.deny` / `permissions.allow`); ours denies `git push`,
  `git merge`, `sudo`, `systemctl`, `npm run build`, `tools/serv-deploy.sh`.
  **Measure it, don't believe it** — an untested denylist is a belief, not a control. Verified
  2026-08-02 with a throwaway repo pointed at a real local bare remote (so a push would genuinely
  succeed if unblocked): `cursor-agent -p --force` told to push returned *"Permission denied: Command
  blocked by permissions configuration"* with `remote commits: 0`, while an allowed command still
  auto-ran. **Compensating control:** record `git rev-parse origin/main` before each dispatch and
  re-check after — across five dispatches, none moved it.
- **`herdr agent prompt <TARGET> <TEXT>`** takes text positionally. `--text` errors as an unknown option.
- **Reading a TUI agent.** `cursor-agent` renders on the terminal's **alternate screen**, so rows that
  scroll away never reach herdr's scrollback and raising `--lines` cannot recover them. When a read comes
  back truncated, ask the agent to write its full response to a temp `.md` and reply with only the path,
  then read the file. Fallback only — don't ask for file output in the initial prompt.
- **CLI error contract:** server errors are JSON on **stderr** with exit 1; syntax errors exit 2.

### ★ One agent per TASK — close the pane when it lands

Founder direction 2026-08-02: *"after each task close the agent pane and spawn a new agent for the next
task — you handle the worktree opening and closing."* The stated reason is **context-window hygiene**: a
long-lived agent carries the full history of tasks it already finished, while a fresh one starts clean on
a self-contained brief. Grok sat at 42.7% context after one round of the dossier work.

- **Review rounds of the same task keep the same agent** — a fix-round dispatch *benefits* from the
  context and it's still one task. Close only when the task is genuinely done.
- The **orchestrator** owns worktree lifecycle and the branch; the agent only writes code in the tree
  it's handed.
- **Write the brief as a FILE in the worktree** and tell the agent to read it, rather than pasting it:
  the agent reads it once instead of carrying it in context, and it sidesteps the composer paste bug
  above entirely. Delete the brief before the round's commit (or leave it untracked).
- `herdr pane close <paneId>` when done — only panes you created.

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
(For a multi-round build, the tree survives the whole thread — §2.)

⚠️ **`/home/mbelli/dev/help_me_trade` is routinely BEHIND `origin/main` — never conclude a file doesn't
exist from it.** It's the live deployed checkout and only advances when someone deploys, so it lags every
round landed from a worktree. Measured 2026-08-02: it sat 11 commits behind while I ran `ls References/`
there and told the founder **this very document didn't exist**. It did — on `main`, four commits of its
own history deep. Check the ref, not the disk:

```bash
git ls-tree origin/main --name-only References/     # what main actually has
git log --all --oneline -- <path>                   # what history says about it
```

That's the `dev-tree-vs-orca-worktree-divergence` trap wearing a different hat: run commands where the
data is, but resolve *questions about the repo* against `origin/main`.

## 6. Related

`References/SERVER_SETUP.md` (Node/env for the bridge), `References/STARTUP.md`,
`tools/composer-bridge/README.md` (the headless fallback path — kept, not the default).
