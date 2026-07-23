# LLM-Managed Paper Books — Program Build Spec (2026-07-23)

*The build spec for the four-book forward-test program the founder green-lit 07-23. Decisions here
were settled in-session 07-23 (accounts wired + verified paper, panel design agreed, shorts
authorized); this doc turns them into buildable increments. Companion surfaces: the Strategy Lab
book pages (shipped 07-23, `frontend/components/workspace/lab/`) already carry honest-empty seams
waiting on the origins defined in §4.*

## 1. What this is (and is not)

A **forward-test study**: can an LLM given a nightly, deterministic information pack run a paper
book worth following? No backtest exists or is possible — the study IS the evidence. Every book is
paper, every entry is founder-approved (propose mode), every idea is ledger-graded whether taken or
not. This is **not** the app's Decision Engine: the mechanical PERCEIVE→REASON→DECIDE pipeline and
these free-rein books are separate products of the same data, kept apart by the valve rules in §2.
(The panel-on-mechanical-setups design — LLM seats reasoning over pipeline-proposed setups — is its
own workstream: `MULTI_ANGLE_REASONING_AB_2026-07-19.md`, awaiting founder go. Same debate shape,
different substrate; neither gates the other.)

## 2. Non-negotiables (inherited, not new)

- **One account per book** — capital walls; per-book P&L is real account P&L, never attributed math.
- **One-way valve**: mechanical signals (fusion bus, flow, watchlist state) may flow INTO a book's
  context pack; book output NEVER writes back into the fusion bus, mechanical lanes, or any
  perception store. Book proposals exist only as proposals + ledger rows in their own lanes.
- **Provenance everywhere**: every proposal, push, report, and UI row names its book + seat.
- **Chokepoint unchanged**: `trading/execution_service.submit()` + `RiskEngine.validate()`; books
  ship as strategy records **disabled + entry_mode=propose**; founder arms manually.
- **Shorts**: authorized for these books ONLY, via a per-strategy `allow_short` flag — its own
  adversarially-reviewed chokepoint round (§8). Until that round ships, books are long-only too.
- **ToS boundary**: Claude Code sessions are the founder's personal tool driving HIS books —
  proposals he approves. Claude Code is never wired as an app LLM provider; if this ever serves
  other users, their books run on API keys through the app's provider-agnostic seam.
- **Push discipline**: ONE batched nightly push per book cycle (auto-entry-alert-batching);
  `alerts.max_push_per_day` stays founder-controlled.

## 3. The four books

| Book | Account (verified live) | Driver | Mode |
|------|------------------------|--------|------|
| Deep Seek | `acct_alpaca_deepseek` | app LLM seam (DeepSeek key already configured) — nightly job in-process | propose |
| Fable | `acct_alpaca_fable` | scheduled Claude Code session, Fable solo | propose |
| Claude Panel | `acct_alpaca_open` | scheduled Claude Code session: Opus seats + Fable judge | propose |
| Matt + LLM | `acct_fc78907f4bda` | **no LLM proposals** — executes founder instructions only; nightly informational snapshot | manual |

Panel design (settled): bull + bear **cross-examine the same candidates** (not parallel monologues),
plus a portfolio/risk seat; **Fable judges** against a pre-registered rubric — evidence quality,
falsifiability (every thesis names the level/date that kills it → becomes the stop), R/R, book fit —
and writes a bear pre-mortem for anything it advances. Seats are Opus, judge is Fable
(agent-model-budget-routing). Driver sessions get a narrow prompt and NO sub-agent spawning
(agent-research-fanout-hazard).

## 4. Ledger lanes & origins (the calibration contract)

Seam: `db/idea_outcomes.register_idea(origin=...)`. New origins REQUIRED (each needs an
`IDEA_COOLDOWN_MIN` entry — direct-index map, unknown origin KeyErrors):

- `book:deepseek`, `book:fable`, `book:panel` — the book's ADVANCED ideas (what it actually proposed).
- `book:panel/bull`, `book:panel/bear`, `book:panel/macro`, `book:panel/risk` — EVERY seat's
  candidates, advanced or not (calibration-counts-everything). Seat lanes are **journal-class**
  (`JOURNAL_ORIGINS`) so they're graded per-lane but never pollute headline grading.
- Nothing for Matt + LLM — no machine ideas exist there.

The Lab book actor page's "no idea-ledger origin maps to this book yet" empty state wires to
`book:<x>` the moment these exist. Grading is ordinal (calibration = filter, not goal): the study's
output is "which book/seat lanes deserve to keep running," not a win% headline.

## 5. Nightly pipeline (after close, per book)

1. **Context pack — deterministic script, no LLM** (`tools/book_context_pack.py`): account snapshot
   (positions, cash, day/total P&L from the book's own account), open proposals, its ledger lane
   summary, market frame (indices, breadth, VIX), relevant mechanical perception (fusion attention
   tiers, flow decomp report pointer, earnings calendar) — the valve's INBOUND side. Output: one
   markdown pack per book (markdown, not JSON — deepseek-llm-lessons). Packs are archived to disk
   per date (`data/book_packs/`, gitignored) so every decision is auditable against exactly what the
   model saw.
2. **Session**: the book's driver reads the pack, may READ app endpoints (read-only bearer), and
   emits a structured decision block: proposals (symbol, side, qty/notional, thesis, falsifier→stop,
   horizon) + a hold/trim review of existing positions + its full candidate list for lane
   registration.
3. **Registration**: every candidate → its lane (§4); advanced proposals → the existing proposal
   intake (`trading/proposal_intake.py`) with book provenance; nightly report (markdown) stored and
   surfaced in the Lab book page.
4. **One batched push** per book: "Fable book: 2 proposals, 1 trim — review by open."
5. **Morning**: founder approves on phone (existing biometric approvals flow) → chokepoint executes
   at/after open. Open question (founder lane): plain market-at-approval vs MOO — MOO matches
   daily-bar assumptions best; VWAP/TWAP are DEAD for paper (Elite-gated + not simulated —
   alpaca-algo-orders-elite-gated).

Matt + LLM variant: steps 1 + a snapshot render ONLY (positions, P&L, concentration, upcoming
earnings for held names). Explicitly no proposals, no candidate lists, no lane rows.

## 6. Scheduling & ops

- Deep Seek book: in-process scheduled job (existing job loop + `/health` jobs registry — it shows
  up in the Lab "agents ran today" module for free). Fires 17:0x ET after the flow_decomp report.
- Fable + Panel: scheduled Claude Code sessions on serv (crontab, project cd, UTC times — serv
  crons are UTC). Each session's LAST act: write its report + registration payload via one
  authenticated POST; a dead-man check flags a book that produced no report by 20:00 ET.
- All three respect the strategy record: `enabled=false` → the job/session runs a DRY cycle
  (pack + report, no proposals) so the founder can review output quality before arming — the
  proving period is free.

## 7. Build increments (each = own round, verify step named)

1. **Plumbing**: strategy records (3 books, disabled/propose) + origins & cooldowns + lane
   registration helper + `book_context_pack.py`. Verify: pack renders for all 4 accounts against
   live paper state; `register_idea` accepts each new origin; targeted pytest module.
2. **Deep Seek book end-to-end** (cheapest driver proves the whole pipe): nightly job → pack →
   DeepSeek → report + registrations + batched push (push held until founder arms). Verify: dry
   cycle produces a real report from live data; lane rows appear; Lab book page shows them.
3. **Matt + LLM snapshot job.** Verify: nightly render matches account state; zero proposal rows.
4. **Fable + Panel sessions**: session prompts (pre-registered judge rubric text lives in this repo,
   not improvised per-night), cron wiring, dead-man. Verify: one supervised live run each; per-seat
   lanes populated; cross-examination visible in the report.
5. **Shorts unlock round** (§8) — only after 1–4 are proven in long-only propose mode.
6. **Lab UI wiring round**: book pages consume `book:<x>` lanes + nightly report display (the
   panel per-seat grading view the founder called "very strong" in the A2 mock).

## 8. Shorts unlock round (separate, adversarially reviewed)

Per-strategy `allow_short` flag (default false, only settable on book strategies for now);
chokepoint distinguishes **sell-to-close vs sell-to-open** (order_executions already carries
`short_open`); RiskEngine grows short-aware caps (gross exposure = longs + |shorts|, per-name short
cap, no shorting into halts/earnings without explicit flag); Alpaca paper handles borrow natively.
Two parallel Opus reviewers with split mandates before commit (reviews-on-opus). The June
"no shorts ever" record is formally revised by this round's BUILD_LOG row (options-strategies
memory already flagged the revision).

## 9. Founder-lane decisions (open)

- Execution style at approval: market-at-approval vs MOO batch at next open (§5.5).
- Panel cadence: nightly vs Tue/Thu (Opus budget vs signal freshness).
- Per-book notional caps at arming time (RiskEngine values, founder sets when arming).
- When (if ever) a book graduates from propose → bounded-auto: NOT in this spec's scope; would ride
  the Rung-3 design (rung3-bounded-auto-design-intel), never a side door.
