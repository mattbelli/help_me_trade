# Git workflow — PC ⇄ serv ⇄ main

*How git is coordinated across the founder's Windows **PC** (this machine — research + Norgate data live here),
the always-on **serv** Linux box (runs the app on `main`), and the shared **`main`** branch. Read this before
any cross-branch git operation. Written after a 2026-06 round where a reflexive `git pull` and a 126-commit
branch drift caused avoidable conflicts + a near-miss broker bug.*

## The model
- **`main` = trunk.** The app, shared code, and committed artifacts. **serv runs on `main`** and pulls it to
  load things (e.g. `research/artifacts/strategy_stats.json` via `signals/strategy_stats.py`). **If something
  must reach serv, it has to land on `main`** — a feature branch is invisible to serv.
- **Research branches** (e.g. `research/decision-engine-data-layer`) = in-flight exploration. They deliver to
  `main` via **small, focused PRs or artifacts**, never a big-bang merge of the whole branch.
- **serv is a peer agent, not the user.** It also works on `main`; its requests are real. But **a serv request
  does not authorize merging to `main`** (see Cardinal Rules #1).

## Cardinal rules (do not violate)
1. **Never self-merge a PR to `main`.** The harness/classifier blocks it by design — only the **user**
   authorizes landing on `main`. Open the PR, hand the user the one-liner `gh pr merge <#> --squash`, and stop.
   "serv said push it" is **not** user authorization.
2. **Branch first off `main` for any commit** — never commit directly to `main`.
3. **Don't `git pull origin main` casually on a feature branch.** It starts a merge; on a drifted branch it
   conflicts mid-tree. Catch up *deliberately* (see below), never reflexively. If a pull leaves a half-merged
   tree you didn't intend, `git merge --abort` to get back to clean.
4. **Keep research branches current — merge `main` in ~weekly.** Drifting 100+ commits behind = researching on
   stale shared code **and** a painful conflict wall (we hit 126 behind → 7 conflicts). Small and often.
5. **Promote proven pieces to `main` in small PRs** (one tool/artifact/doc per PR), not the whole branch.
6. **Adversarially review any `trading/` or `brokers/` change before committing** (CLAUDE.md safety rule) —
   including conflict resolutions in those files. A 2026-06 catch-up merge silently introduced a
   `client_from_manual_flow(interactive=…)` call that doesn't exist in the library → would have crashed Schwab
   re-auth. Re-verify such resolutions against the real library/API, don't rubber-stamp.

## Common operations
**Deliver an artifact to serv** (a file serv auto-loads):
1. The artifact path is usually **gitignored** (`research/artifacts/`, `data/`) → stage with `git add -f`.
2. Validate it first against serv's loader + schema (`signals/*`, `research/artifacts/README.md`).
3. Branch off `origin/main`, add the file, commit, push, open PR.
4. Tell the **user** to merge — you cannot.

**Catch a research branch up to `main`:**
1. `git fetch origin` → `git merge origin/main` on the branch (deliberate, not a reflex pull).
2. Resolve conflicts: docs = union (lose nothing); add/add = superset of both; code = keep both sides' fixes;
   **read `trading/`/`brokers/` resolutions carefully** (re-verify vs the real API).
3. Run the **pre-push gates** (below). Then push the branch to **its own** remote (not `main`).

**Promote a tool/doc to `main`:** branch off `origin/main`, add just that file, PR, user merges.

## Pre-push gates (from CLAUDE.md)
`ruff check .` (whole repo) + `python -m pytest -q` + `cd frontend && npm run typecheck`. Investigate every
red. Known-harmless flake: `test_option_sell_on_expiry_day_allowed` fails on the PC in the evening (local-vs-UTC
date; serv is green in UTC) — see `~/.claude/.../memory/pytest-tz-flake-option-expiry.md`.

## PowerShell / Windows gotchas
- Multi-line commit messages with quotes break here-strings → write the message to a temp file and
  `git commit -F /tmp/msg.txt`.
- Git Bash path-mangles leading-slash futures symbols (`/ES` → a Windows path) → run those CLIs via PowerShell.
- `git merge --abort` is your friend when a pull/merge goes sideways and you weren't ready for it.
