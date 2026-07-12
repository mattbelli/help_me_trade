# CLAUDE.md — how to work on help_me_trade

*Auto-loaded every session: the mission + the working agreements that keep agents consistent here.
Companions: `AGENTS.md` (codebase map), `References/HANDOFF.md` (current focus — read first),
`References/BUILD.md` (plan + dated history), `References/ARCHITECTURE.md` (subsystem tour).*

## Mission — judge every feature against this
help_me_trade is an **autonomous trading agent that works in the background** so the user — a dad with a job —
can step away. It is NOT a manual trading terminal and NOT a generic automation platform: **trading is the
mission**; tasks/alerts/scans/insights are how the agent trades on his behalf. The dashboard is a **cockpit to
configure + supervise the agent and review what it did**, not a place to click trades.

**Autonomy ladder** (climbed as trust grows): (1) alert — tell me, I decide [done]; (2) **propose** — agent
finds + sizes a setup, pings the user's **phone** with Approve/Reject, he taps approve while away;
(3) **bounded auto-execute** — acts within hard guardrails (caps, daily-loss, kill switch) and reports after.
**Phone approval (not auto-execution) is the life-changing unlock.** Success = how little he has to watch it
and still trust it. The hard part is **decision quality + risk + trust**, not automation plumbing (mostly
built). Prioritize agent-decision + risk + phone delivery; deprioritize manual-trader polish (DOM ladder,
greeks chains, fancy chart tools) and especially un-guarded real-money execution.

## How to work (the established cadence)
1. **Delegate coding to Composer — don't hand-edit by default.** Write a precise, self-contained brief (exact
   files, current vs desired behavior, constraints, verify steps) → `node tools/composer-bridge/dispatch.mjs
   --brief tools/composer-bridge/briefs/<x>.md` (backgrounded, **one run at a time** — parallel runs collide
   in the working tree) → review the real `git diff` + lint/tests **yourself** before committing. Hand-edit
   only for surgical fixes or genuinely hard/cross-cutting work. You are the orchestrator (plan/brief/review/
   architecture); Composer is the coder.
2. **Work autonomously — make the call, don't pose menus.** Pick the sensible default and proceed (investigate
   → brief → dispatch → verify → commit). Reserve questions for a genuinely blocking fork with no reasonable
   default (irreversible data loss, a real product-direction choice). Surface *results + decisions made*, not
   option-menus. The user is often remote/away and wants momentum.
3. **Commit cadence: iterate → confirm → BUILD.md → commit.** Don't commit per micro-edit. Build it, leave it
   in the working tree, let the user use + adjust it; once it's good, update `References/BUILD.md` (flip
   status, add a history row), THEN commit + push the consolidated round. For UI-only work, note "pending
   in-browser check" rather than blocking. **Update `References/board.json` in the SAME commit that does the
   work** — a git hook *blocks* any product-code commit that doesn't also touch `board.json`/`BUILD.md` (see
   **Board discipline** below). Same discipline as BUILD/HANDOFF; a stale board defeats its purpose.
4. **Minimize the Opus main-loop token cost** ("you are the expensive one"). Delegate: code → Composer;
   recon/review/research → **Sonnet subagents**; keep the main loop short — don't do heavy generation or broad
   file-reading inline when a subagent can. `/model sonnet` for routine turns is the user's biggest lever.

## Board discipline — what earns a card, and keeping it honest
The kanban (`References/board.json`, rendered at `/board`) is the live source of truth for in-flight work; a
git hook **blocks** any commit that changes product code without also updating `board.json` or `BUILD.md`
(escape hatch: `[no-board]` in the message for a genuine typo/refactor/hotfix).
- **A card must clear all three:** (1) it has a *deliverable* — a code/doc/config diff (if "done" isn't a
  diff, it's not a card); (2) it's *not already on the board* — search first, and if it overlaps an existing
  card, extend that card's notes instead of opening a new one; (3) it's worth briefing someone on (a discrete,
  nameable piece of work, not a micro-edit).
- **Never a card** (this is what bloats it): "watch/monitor X", "keep an eye on Y", "remember to check Z",
  FYIs, observations, status. Those go in the relevant card's notes as a follow-up line, or a memory — never
  their own card.
- **Lifecycle, tied to the commit:** open a card (`in_progress`) when work *starts*, not speculatively; the
  commit (or the consolidated round's commit) that changes the work updates the card in that same commit —
  flip status, move lane, record the outcome. Done + verified → move to `done` (the newest stay visible; the
  frontend collapses the older ones into **Archive**). **Don't delete cards — archive them** (git + BUILD.md
  are the narrative; the board keeps the record). Delete only true junk that never should have been a card.
- **One card per thread** — if two cards are the same thread, combine on sight.
- Decisions only the founder can make go in the `founder` lane ("Waiting on you").

## Engineering discipline (default coding posture — applies to your edits AND the briefs you write Composer)
Bias toward caution over speed on non-trivial work; use judgment on trivial edits.
- **Think before coding.** State assumptions explicitly; if multiple interpretations exist, name them — don't
  pick silently. This *sharpens* rule #2, it doesn't undo it: surface ambiguity about **what/why**
  (requirements, product direction) before building; stay decisive on **how** (don't pose menus on mechanics).
- **Simplicity first.** Minimum code that solves the problem — nothing speculative. No unrequested
  abstractions / config / "flexibility", no error handling for impossible cases. If 200 lines could be 50,
  rewrite. Ask: "would a senior engineer call this overcomplicated?"
- **Surgical changes.** Touch only what the request needs; match existing style even if you'd do it
  differently. Don't refactor/reformat adjacent code. Remove only the orphans *your* change created; flag
  pre-existing dead code, don't delete it. Every changed line should trace to the request.
- **Goal-driven execution.** Turn the task into a verifiable goal and loop until met ("fix the bug" → write a
  failing test, then make it pass). For multi-step work, state a brief plan with a verify step per item.

## Safety — non-negotiable (this trades real money)
- **The execution chokepoint is sacred.** `trading/execution_service.submit()` is the ONLY path to a broker;
  `RiskEngine.validate()` gates every order; kill-switch-first; deny-by-default; **paper before live**; LIVE
  stays hard-blocked until the typed double-gate (Increment 3). **Adversarially review (Sonnet) any change to
  `trading/` or `db/order_executions` before committing** — it's caught ~16 real safety bugs so far.
- **Decimal-only in `trading/`** for money/qty (float→Decimal seam at the propose_order boundary). Ledger +
  executions store money as TEXT/Decimal.
- **Never expose the Alpaca `trading` toolset to the chat agent.** Agent proposes → human approves → the
  chokepoint executes. Sells can only reduce a held long — no shorting.
- **Secrets live in gitignored `.env`; `data/` is gitignored** (real ledger + the user's financial exports —
  NEVER commit user financial data).
- **Net-cash realized P&L is the source of truth; FIFO is unreliable** on the real Schwab data (demoted to
  approximate) — lead with net-cash, flag FIFO. Don't surface fabricated numbers/preferences to the user.
- Don't use a Claude subscription/OAuth for the app's LLM (Anthropic ToS); the app uses provider-agnostic keys.

## Pointers & pre-push
- **★ The app's headline value = the Decision Engine** (PERCEIVE→REASON→DECIDE): the agent fusing the best data
  sources into *ranked, explained, fully-planned* trade ideas (why + calibrated win% + entry/stop/scale-out
  targets). Delivery + execution are the strong foundation; **decision quality is what makes the app worth
  using.** See `References/BUILD.md` → "★ The Decision Engine" section. Win%/ranks must be **calibrated, never
  fabricated.** Active founder+orchestrator deep-research effort — do not treat as a someday-footnote.
- `References/CODE_AUDIT.md` (cleanup backlog), `References/WATCHLIST_SPEC.md` (next feature),
  `References/SERVER_SETUP.md` (the home Linux server this now runs on).
- **Kill any dev/smoke server you spawn when you're done.** Headless-UI/screenshot checks spin up a Next
  dev server (port 3005+) on the shared box; left running it leaks to multiple GB of RAM over days. Tear
  down the one you started — don't leave zombies eating the host's memory.
- **Before pushing:** `ruff check .` (whole repo) + `python -m pytest -q` + `cd frontend && npm run typecheck`.
