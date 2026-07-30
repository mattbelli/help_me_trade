# CLAUDE.md — how to work on help_me_trade

*Auto-loaded every session: the mission + the working agreements that keep agents consistent here.
Companions: `AGENTS.md` (codebase map), `References/HANDOFF.md` (OPEN threads — read first),
`References/BUILD.md` (the plan), `References/BUILD_LOG.md` (dated journal — grep, never read whole),
`References/ARCHITECTURE.md` (subsystem tour).*

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

## ★ THE BEST IDEAS WIN — the reasoning loop for anything that matters (founder directive 2026-07-16)
On **architecture decisions, real problem solving, and high-importance edits**, do NOT emit the first
coherent answer. Run the loop: **reason → check → reason → check → reason → check** — every "check" tests
the current best answer against something REAL (the code as it is, live data, a measurement, an adversarial
read), and each pass either survives the check or changes because of it. Stop when another check stops
changing the answer — never merely when the answer sounds right. A basic first-thought mechanism has no
edge; token count never gates this loop (rule 4's cost discipline covers mechanics, not thinking).

**When you think there isn't a solution, the dead end is evidence about your METHOD, not the problem.
Switch thinking methods and go again.** Switches that have already won here:
- **Invert the question.** "What plan gives us all quotes?" was unsolvable; "what removes the need for
  standing quotes at all?" had answers.
- **Turn walls into variables.** A "forbidden" heuristic is a measurable method — honesty rules constrain
  what we PRESENT to the user, never what we measure against ground truth to learn an error rate.
- **Inventory adjacent ingredients.** The winning pieces are often already in this repo, walked past
  because doctrine anchored you (the tick test and the poll lane sat here the whole time).
- **Run a zero-context pass.** Spawn a fresh agent given ONLY the problem statement, no repo context —
  fresh eyes see past walls the repo's own doctrines build (a basic external chatbot once out-generated
  the deeply-anchored main loop; never again).
- **Work backwards from the ideal.** Assume the perfect mechanism exists, describe it, then find which
  constraint supposedly forbids it — and CHECK whether that constraint is measured reality or inherited
  assumption.
Never conclude "impossible," "no edge available," or "we must buy it" until at least two DIFFERENT thinking
methods have failed and the failure is measured, not assumed.

## How to work (the established cadence)
1. **Route the CODER by stakes — quality first; writing slop 10x faster loses to well-thought-out slow
   development every single time** (rewritten 2026-07-16; was "delegate to Composer by default," which
   enshrined the fast-slop default). The bench, all available: **Claude Max plan** — Opus/Sonnet coding
   subagents, or the orchestrator hand-editing when full-context surgical work is the right tool; **Cursor
   plan** — Composer, plus Opus/Sonnet/Grok 4.5 routed through it (`node tools/composer-bridge/dispatch.mjs
   --brief tools/composer-bridge/briefs/<x>.md`, backgrounded, **one tree-writer at a time** — parallel runs
   collide in the working tree). Route like rule 4: load-bearing or subtle work → a frontier coder with
   full context; mechanical/boilerplate → Composer/Sonnet is fine. What never changes regardless of coder:
   a precise, self-contained brief (exact files, current vs desired behavior, constraints, verify steps),
   and YOU review the real `git diff` + lint/tests before committing — review is where depth concentrates.
   You are the orchestrator (plan/brief/review/architecture); pick the coder the work deserves.
2. **Work autonomously — make the call, don't pose menus.** Pick the sensible default and proceed (investigate
   → brief → dispatch → verify → commit). Reserve questions for a genuinely blocking fork with no reasonable
   default (irreversible data loss, a real product-direction choice). Surface *results + decisions made*, not
   option-menus. The user is often remote/away and wants momentum.
3. **Commit cadence: iterate → confirm → BUILD_LOG row → commit.** Don't commit per micro-edit. Build it,
   leave it in the working tree, let the user use + adjust it; once it's good, add a `References/BUILD_LOG.md`
   history row (past tense, the record) and update `References/HANDOFF.md` (open items ONLY — tense rule:
   HANDOFF never re-narrates shipped work, it points at the log row; delete entries when threads close), THEN
   commit + push the consolidated round. For UI-only work, note "pending in-browser check" rather than
   blocking. **Update `References/board.json` in the SAME commit that does the work** — a git hook *blocks*
   any product-code commit that doesn't also touch `board.json`/`BUILD.md`/`BUILD_LOG.md` (see **Board
   discipline** below). A stale board defeats its purpose.
4. **Route depth by stakes, never by task size** (recalibrated 2026-07-16 — the old rule here, "minimize
   the Opus main-loop token cost," optimized the wrong variable and probably cost us quality: the exit
   engine shipped dead behind a shallow test assumption; the wildcard fiction was recorded as fact and
   shaped a committed design; the 07-15 Sonnet→Opus review upgrade already proved cheaper single passes
   missed disjoint real bugs). The axis is **load-bearing vs mechanical**, not "routine turn" vs not — a
   one-line constant can be load-bearing (the 0DTE skip), a 500-line widget can be mechanical.
   - **Always maximum depth**: `trading/` + risk + execution; architecture decisions; anything feeding the
     Decision Engine; and ANY claim about system state that will be recorded as fact (memories, docs,
     board, design inputs) — cheap-model recon may *locate* things, but its assertions are hypotheses
     until verified against code or live state.
   - **Cheap stays right for**: mechanical edits, formatting, file-location sweeps, UI polish iterations.
     Delegation (Composer for code, Sonnet for sweeps) survives — depth concentrates in the REVIEW of
     delegated work, which is where it pays.
   - **Under budget pressure, drop scope — never depth on load-bearing work.** A smaller thing built
     deeply beats a bigger thing built shallow; a shallow mechanism has no edge (see ★ THE BEST IDEAS WIN).

## Board discipline — what earns a card, and keeping it honest
The kanban (`References/board.json`, rendered at `/board`) is the live source of truth for in-flight work; a
git hook **blocks** any commit that changes product code without also updating `board.json`, `BUILD.md`, or `BUILD_LOG.md`
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
  frontend collapses the older ones into **Archive**). **Don't delete cards — archive them** (git + BUILD_LOG.md
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
- **★ WIRE IT EVERYWHERE — a data source is NOT done when the UI can see it** (founder directive 2026-07-30).
  New data/perception work must land on **every** consumer surface, and the one silently skipped is always the
  **agents**: register it in `ai/perception/resources.py` (`RESOURCES` entry + reader), then confirm the analyst
  toolsets actually reach it (`research/book_tools.py` for the in-process books; `tools/book_session.sh`
  `--allowedTools` for headless Claude sessions — those had NO perception access at all). Measured cost of
  skipping it: the macro substrate (`signals/macro_substrate.py` — full curve, DFII10, HY/IG OAS) shipped to a
  UI card only, so `query("market")` still returns term-spread+VIX and no agent can see credit spreads; and the
  blind book sessions burned 7 Opus sub-agents in ONE night (35M tokens, 84 min) scraping stockanalysis.com for
  OHLC already sitting on disk. An agent that can't query our data will go buy worse data from the internet.
  **Definition of done for data work: the UI surface + the agent surface + the doc that names it.**

## Safety — non-negotiable (this trades real money)
- **The execution chokepoint is sacred.** `trading/execution_service.submit()` is the ONLY path to a broker;
  `RiskEngine.validate()` gates every order; kill-switch-first; deny-by-default; **paper before live**; LIVE
  stays hard-blocked until the typed double-gate (Increment 3). **Adversarially review any change to
  `trading/` or `db/order_executions` before committing — TWO parallel Opus reviewers, split mandates**
  (the 07-15 Sonnet→Opus upgrade retired single Sonnet passes; see `review-agents-opus`) — this review has
  caught ~16 real safety bugs so far.
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
- **★ The product is CONNECTING THE DOTS, not proving edge per mechanism.** The value is *multiple angles on one
  trade* — flow prints here → what's the underlying's volume? is this a support/resistance level? what do the
  fundamentals say? what are social threads saying? — fused into a picture the user reasons over and **decides**
  on. The user makes the call; we supply the angles, not a verdict. **So: "this signal has no proven edge" is
  NEVER an argument against building a perception source.** Nobody has clean standalone edge in any one
  mechanism; demanding it per-component is a thought-terminating move that kills the fusion layer that *is* the
  product. Edge-hunting (backtests, validation) is **one workstream, not the gate on all others.** What the
  honesty rules actually constrain is **presentation, not construction**: never dress an unvalidated angle as a
  scored/validated signal, never fabricate a win% — label it as perception and let the user weigh it. Build the
  angle; be honest about what it is.
- `References/INDEX.md` — the doc map: which References/ docs are CURRENT vs historical (hook-enforced on
  new docs). Also: `CODE_AUDIT.md` (cleanup backlog), `SERVER_SETUP.md` (the home Linux server).
- **Kill any dev/smoke server you spawn when you're done.** Headless-UI/screenshot checks spin up a Next
  dev server (port 3005+) on the shared box; left running it leaks to multiple GB of RAM over days. Tear
  down the one you started — don't leave zombies eating the host's memory.
- **Before pushing:** `ruff check .` (whole repo) + `python -m pytest -q` + `cd frontend && npm run typecheck`.
