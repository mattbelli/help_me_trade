# GPT-5.6 Sol external audit — overarching product direction (2026-07-17)

*Zero-context read-only audit via the Cursor bridge (model `gpt-5.6-sol`, 263s). Brief:
`tools/composer-bridge/briefs/audit-product-direction-2026-07-17.md` — the founder's vision
statement encoded as the yardstick. Companion to the same-day technical audit
(`AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md`). Historical record.*

---
# 1. Overall read

## What this repo honestly is

`help_me_trade` is a serious single-user trading operating system with:

- unusually broad market perception;
- a hardened paper-execution spine;
- strong phone approval;
- a real composable desktop;
- substantial research and calibration infrastructure;
- but an incomplete decision brain.

It is presently closer to an **instrumented trading platform with an emerging autonomous analyst** than a self-improving autonomous trading assistant. PERCEIVE, ACT, and DELIVER are materially ahead of REASON, DECIDE, and LEARN (`AGENTS.md:17-35`; `References/BUILD.md:29-39`).

The mission is unusually coherent: reduce required attention while increasing trust, rather than building another manual terminal (`CLAUDE.md:8-20`; `References/BUILD.md:15-21`). Most architectural choices support that mission.

## Where it already has an edge

1. **The execution spine is real.** All orders converge on `ExecutionService.submit()`, through kill-switch-first risk validation, with live execution still hard-blocked (`trading/execution_service.py`; `trading/risk_engine.py`; `CLAUDE.md:120-128`). Most “AI trading copilots” do not have this level of explicit safety architecture.

2. **Phone approval is a credible product wedge.** Native mobile has biometric approval, payload hashes, stale-proposal handling, reconciliation, push and Live Activities (`mobile/src/features/approvals/proposal-card.tsx`; `mobile/widgets/trade-proposal.tsx`; `References/BUILD.md:60-62`). This directly serves the “away from screens” vision.

3. **The perception breadth is unusually strong.** The repo owns options-flow and equity-tape collectors and combines those with filings, earnings, social, regime, levels, RVOL and account context (`AGENTS.md:8-15,26-32`). That breadth can become a defensible substrate if the decision layer becomes trustworthy.

4. **The desktop is genuinely composable.** There are 30 registered widgets, multi-page workspaces, linked symbol channels, configurable layouts and persisted workspaces (`frontend/lib/workspace/widget-registry.tsx:63-309`; `frontend/components/workspace/workspace-grid.tsx`; `AGENTS.md:38-49`). This is not a mock dashboard.

5. **The nightly research path is substantive.** `ai/research/setup_scan.py` combines deterministic structure detection, LLM review, ranking, artifacts and ledger registration; `ai/research/engine_v2.py` provides a separate deep-research path (`AGENTS.md:31-32`; `ai/research/setup_scan.py:424-499,989-1181`).

6. **The intellectual direction of the Idea Engine is excellent.** “The pipeline’s output is the ledger, not the ping” is the correct foundation for a trustworthy system (`References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:168-170`). Conservation accounting, shadow cohorts, fixed grading laws and external liveness checks are stronger than typical trading-product thinking.

## What worried me

The repo’s documentation sometimes describes designed or deployed infrastructure as a functioning learning system. It is not one yet.

A read-only database check during this audit found:

- 2,204 convergence notifications;
- zero `idea_outcomes` rows with `origin='convergence'`;
- only 16 total idea rows, including two synthetic canaries.

That directly contradicts “idea-outcome grading of EVERY idea” in `References/BUILD.md:78-84`. The same gap is documented in `References/AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md:17-23`.

The product’s most important promise—“something happened, the assistant investigated it, here is the reasoned result”—is still principally a design. `signals/idea_triggers.py`, `signals/dossier.py`, `research/reason.py`, `signals/idea_delivery.py`, and `signals/idea_calibration.py` do not exist, although they are central components of the governing design (`References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:36-54`).

My blunt assessment: **the platform foundation is stronger than the product currently sitting on it.**

# 2. Design issues

## The codebase fights the vision in several places

### Detection still turns too directly into interruption

Current convergence emits notifications without investigation or ledger registration (`signals/options_flow/convergence.py:552-606`). The Fusion spec measured roughly 850–1,000 cards per day after tape expansion (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:15-29`).

That is the opposite of respecting attention. The system is watching more so the user has to watch more.

### The main learning signal is missing

The dominant notification lane produces no mechanically graded history. Existing `lane_stats` has no convergence rows, while `idea_outcomes` contains only a tiny set of structure, vetoed, strategy and canary ideas. The self-improving loop therefore cannot currently weed out the product’s noisiest idea type (`db/idea_outcomes.py`; `db/lane_stats.py`; `signals/options_flow/convergence.py`).

### Designed transport is incompatible with current storage

Both governing designs require stable integer cursors, but the named flow and tape stores use text primary keys and timestamp polling (`db/options_flow.py:33-57,266-268`; `db/equity_tape.py:31-46,152-154`; `References/AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md:33-39`). This must be resolved before building the evidence bus; otherwise replay and conservation semantics will be invented during implementation.

### Single-user assumptions are pervasive

The platform has one shared token, one settings file, one database, global workspace state and a hardcoded memory identity (`api/auth.py`; `config/settings.py`; `db/workspace.py:17-21`; `ai/memories/__init__.py:26`).

`References/ALPHA_PREP_SPEC.md:255-281` identifies 14 single-tenant subsystems. Multi-account support is not multi-user support: accounts still share identity, settings, memory, notifications and execution authority.

The near-term DB-per-tenant recommendation is sensible (`References/ALPHA_PREP_SPEC.md:283-307`). Retrofitting `user_id` filters throughout the current SQLite code would create an unacceptable financial-data leakage surface.

### Workspace persistence will not scale

Workspace state is stored as global last-write-wins blobs with no revision or tenant key (`db/workspace.py:17-21,309-324`). The frontend also maintains transitional localStorage and server copies (`frontend/lib/workspace/use-workspace-store.ts:317-351`).

This works for one founder. It will produce cross-device conflicts and destructive overwrites for multiple users or concurrent sessions.

### Product surfaces are fragmented

Desktop has banners, a bell, a notification widget and proposal widgets. Native mobile has a cleaner unified Inbox. Mobile web is a separate fixed four-tab implementation and does not expose workspace composition (`frontend/components/workspace/mobile-workspace.tsx:61-99`; `mobile/src/features/inbox/inbox-screen.tsx`).

There are also legacy standalone pages and multiple chat surfaces (`AGENTS.md:42-49`). This creates competing mental models for where the “assistant” lives.

### Approval posture differs by surface

Native mobile uses device authentication and fail-closed handling (`mobile/src/features/approvals/proposal-card.tsx`). Web approval is essentially a direct button action (`frontend/components/workspace/pending-proposal-card.tsx:70-100`). Web chat also has a distinct immediate-submit lifecycle (`ai/chat/tools.py:1001-1085`).

All paths still reach the execution chokepoint, but trust semantics should not change depending on which card the user happens to click.

### Offline validation is not connected strongly enough to runtime enablement

The Norgate boundary is clean: raw data stays offline and only artifacts ship (`research/norgate_loader.py:1-13`; `research/artifacts/README.md`; `References/BUILD.md:181-195`).

However, passing validation does not mechanically gate strategy enablement. Strategies are code-seeded, runtime parameters are loosely stored as JSON, and enabling is a separate manual operation (`db/strategies.py`; `api/strategy_routes.py`; `signals/strategy_kinds.py`). The board also records severe backtest/live fidelity problems in `earnings-drift-breadth-and-entry-timing` (`References/board.json`).

### Documentation is ahead of operational truth

`References/BUILD.md:304-308` says Phase 1 is live while the acceptance gate remains open and the audit verdict is FIX-FIRST (`References/AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md:81-85`). `References/ARCHITECTURE.md` is dated June 20 and still describes superseded chat paths (`References/ARCHITECTURE.md:1-5,70-94`).

For an AI-agent workforce, stale architecture documents are operational defects, not clerical issues.

# 3. Correct shape for each pillar

## a. Alerting that respects attention

Use four strict lanes:

1. **Protective interruption:** held-position risk, failed execution, kill switch, stale account state. Never budgeted.
2. **Actionable proposal:** investigated, current, fully planned and eligible for approval.
3. **Digest:** useful but non-urgent findings, grouped by episode and symbol.
4. **Silent evidence:** everything retained for grading and dashboard exploration.

Detectors should not decide delivery. The sequence should be:

`event → episode → investigation/plan → ledger → staleness check → delivery policy → notify`

Keep `signals/notify.py` and `signals/push.py` as delivery chokepoints, but place a single idea composer and attention policy before them. The design already specifies this in `References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:103-117`.

The primary product metric should be **useful decisions per interruption**, not alert count.

## b. Event-driven investigation

Implement the governing episode architecture, after fixing its transport contract:

- add a stable append sequence to each event store;
- cursor-poll from the backend across all collector processes;
- coalesce by symbol and episode;
- score deterministically before spending LLM budget;
- build a persisted evidence dossier;
- run bounded triage and investigation;
- validate a probability-free plan;
- register before any delivery decision.

Do not fetch external data inline during the market-open burst. Persist enough quote/ATR context at trigger time to reproduce the plan later. This resolves gaps identified in `References/AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md:33-39`.

## c. Nightly agentic research sessions

Keep `ai/research/setup_scan.py` as the breadth engine, but turn its output into a session rather than a report tombstone:

- nightly brief: what changed, what deserves review, unresolved contradictions;
- structured idea cards linked to evidence;
- one continuing chat session grounded in that exact artifact;
- explicit “research further,” “not interested,” “watch,” and “prepare plan” actions;
- next-night follow-up on prior decisions.

Nightly batch research and intraday event investigation should share dossiers, ideas and memories, but remain separate schedulers. One optimizes breadth; the other latency.

Registration must be part of job success, not fail-soft bookkeeping (`ai/research/setup_scan.py:1161-1203`).

## d. Learning the user

Separate three concepts:

- **Preference:** what the user likes researching or trading.
- **Constraint:** risk, instruments, holding period, account purpose.
- **Quality:** what subsequently worked.

Do not treat “taken” as proof of quality or “skipped” as rejection of the idea type. Capture lightweight reasons: timing, capital unavailable, already exposed, disliked thesis, wrong horizon, or simply did not see it.

Build a derived ordinal taste profile from:

- `user_action`;
- founder verdicts;
- skip reasons;
- conversation memories;
- research dwell/actions;
- repeated lane and horizon choices.

Existing memory extraction and authority controls are useful (`ai/chat/memory_extract.py`; `ai/memories/judge.py`; `ai/chat/context.py`). But memories are not a taste model, and current proposal dispositions do not feed ranking or delivery (`trading/proposals.py`; `db/idea_outcomes.py`).

## e. Self-improving idea loop

The minimum valid loop is:

`register every plan → grade mechanically → audit missing dispositions → compare lanes ordinally → alter investigation/delivery budgets → continue shadow sampling`

Keep quality learning and preference learning distinct.

Use:

- frozen lane definitions;
- horizon-appropriate grading;
- market-session calendars;
- cluster-aware effective sample sizes;
- suppressed/random shadow cohorts;
- regime slicing;
- explicit versioning whenever plan geometry changes.

The system should tighten attention thresholds before changing strategy logic. Automatic rule mutation is premature. The governing design’s apprenticeship model is directionally correct (`References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:119-136`).

## f. Widget dashboard

The desktop promise is already largely delivered. Preserve:

- free placement;
- named workspaces and pages;
- linked symbol channels;
- multi-instance widgets;
- theme control.

Improve architecture by moving widget-specific headers and controls out of `workspace-grid.tsx`, lifecycle-suspending hidden heavy widgets, and adopting one shared typed idea payload across dashboard, chat and phone (`frontend/components/workspace/workspace-grid.tsx`; `frontend/lib/workspace/widget-registry.tsx`).

Do not put the desktop grid on the phone. Native mobile should remain mission-specific: Inbox, approve/reject, watchlist, brief, chat. “Creative freedom” belongs at the research desk; speed and certainty belong on the phone.

## g. Strategy import and paper forward-test without licensed data

Do not accept arbitrary Python plugins.

Create a versioned **Strategy Pack**:

- declarative strategy ID and parameter schema;
- supported built-in signal primitives;
- universe assumptions;
- entry/exit specification;
- data requirements;
- validation report;
- source-data provenance;
- artifact hash and strategy version.

Users get two paths:

1. Run an open local validator against data they legally possess, then import only the signed report/artifact.
2. Import a strategy without historical evidence as “unvalidated,” eligible only for shadow/paper forward testing.

The hosted app then:

`schema-validates → replays small public/synthetic conformance fixtures → shadows → paper forward-tests → reports evidence → offers promotion`

Raw Norgate data never leaves the founder’s PC. Existing artifact separation is the right precedent (`research/norgate_loader.py`; `research/artifacts/README.md`; `References/NORGATE_PULL.md`). Runtime enablement should require a matching validated artifact or an explicit override.

# 4. Sequencing

For one founder using AI agents, I would sequence:

1. **Restore truth in the current loop.** Close the Phase 1 gate: dead-man, real canary traversal, convergence registration, reliable registration counts, exchange-calendar grading and honest phone calibration (`References/AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md:13-27,41-47,61-79`).

2. **Build the observable evidence substrate.** Stable event cursors, episodes, dispositions, conservation and the rough Evidence Stack widget. No LLM pushes yet.

3. **Add one event-driven investigation lane.** Start with one high-value, low-volume archetype. Prove event → dossier → reason → ledger → digest before adding source breadth.

4. **Unify the research experience.** Nightly brief, artifact-grounded chat and user-action capture.

5. **Close ordinal learning.** Lane scorecards, skip reasons and attention-threshold adjustment. This is where compounding begins.

6. **Add Strategy Packs and paper forward testing.** First for built-in primitives; user-imported strategies remain sandboxed and declarative.

7. **Run a tiny physically isolated alpha.** Separate DB/process per tester as proposed in `References/ALPHA_PREP_SPEC.md:10-37`. Learn product behavior before building full tenancy.

8. **Only then build cloud tenancy and bounded live execution.** These are independent expensive tracks and neither creates decision quality.

## Cut or defer now

- Bounded real-money auto-execution (`References/BUILD.md:309-310`).
- Cardinal win-probability work; retain “unproven” and ordinal lane ranking (`References/BUILD.md:132-141`).
- Memory overhaul beyond correctness fixes (`References/BUILD.md:297-302,326-328`).
- More watchlist/theme/dashboard polish; the desktop is already ahead of the brain (`References/board.json`, cards `watchlist-columns-v3`, `theme-editor-knobs`).
- Additional perception feeds except fixing known data-quality failures and making the options-data-plan decision (`References/board.json`, cards `flow-chain-pool-truncation`, `options-data-plan-decision`).
- Full multi-tenant SaaS architecture.
- Arbitrary user-authored strategy code.

# 5. Blind spots

1. **Regulatory product posture.** The repo has extensive execution safety and software licensing thought, but little visible treatment of investment-adviser implications, suitability, disclosures, record retention or jurisdictional differences. This becomes urgent before outside users receive personalized proposals (`References/ALPHA_PREP_SPEC.md:375-405`).

2. **Attention effectiveness is not measured.** Push budgets exist, but there is no clear product metric for interruption cost, time-to-understand, missed important alert, or post-approval regret (`signals/push.py`; `db/notifications.py`).

3. **Binary taken/skipped data is semantically weak.** Without reasons, the system will confuse taste, capital constraints, timing and idea quality (`trading/proposals.py`; `db/idea_outcomes.py`).

4. **Portfolio-level idea quality is underdeveloped.** Independent ideas can all be individually reasonable while creating one correlated portfolio bet. The roadmap recognizes a future allocator, but current learning is mostly per-idea (`References/BUILD.md:252-274`).

5. **Strategy provenance needs stronger versioning.** Code version, parameters, feature definitions, data vintage and grading law must travel together. Otherwise “the same lane” silently changes underneath accumulated evidence (`signals/strategy_kinds.py`; `research/artifacts/README.md`).

6. **A push receipt is not user comprehension.** Transport success, notification opening, card reading and informed approval are different events. The trust loop currently overweights delivery mechanics.

7. **Home-server concentration risk.** Backend, SQLite, collectors, research jobs and user state remain concentrated on one machine. The external dead-man helps detect failure but does not provide continuity (`AGENTS.md:8-15`; `References/SERVER_SETUP.md`).

8. **The repo risks becoming specification-rich and verification-poor.** The current Idea Engine is an example: excellent design, very small real sample population, and an overstated “LIVE” label (`References/BUILD.md:78-87`; `References/AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md:81-85`).

The highest-value move is not more breadth, automation or polish. It is making one complete loop undeniably real: **meaningful event → autonomous investigation → concise explanation → logged decision → mechanical outcome → less noise next time.**
