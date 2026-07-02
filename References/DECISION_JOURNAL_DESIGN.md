# Decision Journal — design (2026-07-01)

*The P4 "acts on what it learns" core — the substrate that lets the agent reference and learn from its own
track record so it feels like a co-pilot, not a stateless prompt. Design-first (per BUILD.md §4.2); implement
in phases, adversarially reviewed (touches `trading/`).*

## The problem (grounded in a codebase recon)

The agent **cannot answer "what happened to the trades I proposed?"** today. Two disjoint worlds:

- **Decision world:** `paper_order_proposals` (`db/paper_orders.py`) → `order_executions` (`db/order_executions.py`,
  linked by `proposal_id`) → `managed_positions` (`db/managed_positions.py`, linked by `entry_execution_id`).
  This is the agent's own paper-trading chain.
- **Outcome world:** `trade_events` → `realized_pnl` (`db/realized_pnl.py`), a **separate account model**
  (`trade_accounts`, ledger-synced from the broker) with **no `proposal_id`/`execution_id`/`managed_position_id`
  foreign key** back to the decision world. P&L is symbol-level FIFO, not per-decision.

Concrete gaps the recon found:
1. **Fill price is never persisted** anywhere (`order_executions`, `managed_positions`, `order_snapshots` all lack it) — read live off broker snapshots and discarded.
2. **No exit/close execution link** on `managed_positions` (only `entry_execution_id`); closing fills are unlinked.
3. **No P&L / R-multiple / win-loss / holding-time** is computed against the decision chain — anywhere.
4. **Thesis is dropped:** `TradeCandidate.rationale` exists in memory but `propose_trade` never persists it (`trading/proposal_intake.py`).
5. No `decided_by` actor column; no MODIFY path (approve-as-proposed or reject only).
6. Both learn loops (`ai/retrospective.py` on-demand, `ai/reflection/` 24h) read the **ledger/broker-snapshot** world, never the proposal/execution/position chain. *The agent has no memory of its own calls.*
7. The perception `proposals` resource only lists **pending** proposals — no decided/executed history is queryable.

The good news the recon also found — we don't start from zero:
- `paper_order_proposals.score_json` **already persists the decision-scorer snapshot at propose time** (win%/EV/CI/factors, or backtested strategy conviction + plan). The "why/how confident" is captured.
- `paper_order_proposals.decided_at` is already stamped on every terminal transition.
- Clean write choke-points already exist: `create_phone_proposal` (propose), `approve/reject_proposal` (decide), `record_strategy_fill` (entry fill), `close_position` (close).

## The design

**One append-friendly `decision_journal` row per proposal**, written in three write-once stages so **the
reasoning is recorded before the outcome is known** (the non-fabrication guarantee): CONTEXT at propose (never
rewritten), DECISION at decide, OUTCOME when the trade resolves. Outcome is computed **entirely inside the paper
world** from entry/exit fill prices — we do NOT try to reconcile against the separate Schwab/ledger P&L. This is
the agent grading *its own paper calls*, cleanly self-contained.

### Table: `decision_journal` (`db/decision_journal.py`)

Money/qty/prices as **TEXT (Decimal)**, matching `order_executions` + the CLAUDE.md money rule.

```
id                TEXT PK          -- dj_{uuid12}
proposal_id       TEXT UNIQUE NOT NULL   -- anchor → paper_order_proposals.id

-- CONTEXT  (write-once at propose; immutable)
created_at        TEXT NOT NULL
symbol, side      TEXT NOT NULL
qty               TEXT NOT NULL    -- Decimal
order_type        TEXT NOT NULL
limit_price       TEXT
source            TEXT             -- agent_scan | strategy:<id> | exit:<id>
strategy_id       TEXT
rule_id           TEXT
entry_ref_price   TEXT             -- live price at propose (r-multiple basis)
thesis            TEXT             -- rationale/why  (NEW: plumbed from TradeCandidate.rationale)
score_json        TEXT             -- copied from proposal.score_json (win%/EV/CI/factors/basis)
plan_json         TEXT             -- stop/targets/holding from strategy_plan (if any)
expires_at        TEXT

-- DECISION  (write-once at decide)
decision          TEXT             -- approved | rejected | expired | modified
decided_at        TEXT
decided_by        TEXT             -- phone | web | auto:<strategy>   (NEW)

-- OUTCOME  (filled when the trade resolves)
entry_execution_id TEXT
entry_fill_price   TEXT            -- (NEW plumbing)  Decimal
qty_filled         TEXT
exit_execution_id  TEXT            -- (NEW linkage)
exit_fill_price    TEXT
realized_pnl       TEXT            -- Decimal
r_multiple         REAL            -- signed (exit-entry)/(entry-stop)
outcome            TEXT            -- open | win | loss | scratch | never_filled | closed_incomplete
holding_days       REAL
close_reason       TEXT            -- stop | target | time_cap | closed_externally | ...
resolved_at, updated_at  TEXT
```
Indexes: `(symbol)`, `(strategy_id)`, `(outcome)`, `(decision)`, `(created_at DESC)`.

**Journal ≠ Memory.** `memories` stays the curated, human-editable store of *lessons/preferences* (regex-retrieved
today; mem0 vector currently inert — see BUILD.md §5). The **journal is the raw, immutable factual ledger of
decisions + outcomes.** The J3 review loop is what *turns journal facts into memory lessons* + calibration checks.
That separation is the honest answer to "how does it store memory + trades + act on them": **memories = what it
learned; journal = what it did and how it turned out; the review loop bridges them.**

## Build phases

### J0 — Enabling plumbing (small, prerequisite) — **adversarial review (touches `trading/`)**
- **Capture fill price + filled qty.** `fill_tracker.reconcile_open_executions` already reads the broker order; also
  read `filled_avg_price`/`filled_qty` and persist them → add `avg_fill_price`,`filled_qty` cols to `order_executions`.
- **Link the closing execution.** Add `exit_execution_id` to `managed_positions`; set it in `exit_engine` when the sell resolves.
- **Persist the thesis + actor.** Plumb `TradeCandidate.rationale` through `propose_trade → create_phone_proposal →
  create_pending_proposal`; add `decided_by` on the decide path.

### J1 — Journal table + write path (record-only) — **adversarial review**
- `db/decision_journal.py` (table + `record_decision`/`update_decision`/`resolve_decision`/query fns).
- Hooks: `create_phone_proposal` (CONTEXT) · `approve/reject_proposal` + expiry (DECISION) · `record_strategy_fill`
  (entry OUTCOME) · `close_position` (final OUTCOME + compute `realized_pnl`/`r_multiple`/`outcome`/`holding_days`).
- **Honesty limits, stated in the data:** outcomes are reliable for exits routed through `ExecutionService` (the
  agent's own sells — we have an exit fill price). **Reconcile-closures** (position vanished from the broker
  snapshot) have **no exit price** → `outcome='closed_incomplete'`, P&L null. Never fabricate a P&L we can't source.
  All writes fail-soft — a journal write must NEVER break the execution/approval path.

### J2 — Surface it (read path) — no `trading/` touch
- **Perception:** add `RESOURCES["decision_journal"]` (`ai/perception/resources.py`) — query by symbol / strategy /
  outcome / recent. (The maintenance contract in that file already says: new data source → one `RESOURCES` entry.)
- **Chat context:** inject a compact "your recent decisions here" block per turn (`ai/chat/context.py`, mirroring
  `turn_memory_context`) — e.g. *"Last 3 dip_buy proposals on NVDA: 2 target, 1 stop (+1.4R avg)."* This is what makes
  the copilot feel like it remembers its own calls.
- `GET /api/journal` (grows the existing `performance_routes` router); a journal **widget** is deferred (P8-adjacent).

### J3 — Learn / feedback (the payoff) — no silent mutation
- A **journal-review pass** on the existing 24h reflection cadence (`ai/reflection/scheduler.py`): compute the agent's
  **realized calibration** — proposed win% vs actual outcome per strategy and per score-bucket — with **sample counts +
  CIs; "insufficient data" until enough** (same honesty bar as the scorer).
- Output = (a) `lesson` memories seeded via `MemoryService.add` (as retrospective already does), and (b) a **calibration
  drift report** (predicted vs realized) surfaced for the founder. **Never silently retune a strategy or scorer** — the
  learning layer *observes and proposes*; a human arms any change (the standing "strategies are frozen; learning never
  mutates them" rule). This closes the ★ Decision Engine loop honestly: live outcomes validate/challenge the backtest.

## Out of scope (now)
- **Named persona / "Hermes" identity** — founder deferred it; the journal is the substrate a persona would draw on, not the persona itself.
- **MODIFY-before-approve** — doesn't exist today; add later if wanted (would add a `modified` decision + a delta record).
- **Bridging to the Schwab/manual-trade ledger** — the journal is about the *agent's* paper calls; the human's Schwab history stays the retrospective's job.

## Open questions for the founder
1. **Feedback aggressiveness (J3):** just surface calibration drift + lessons (safe default), or also auto-*propose* sizing/threshold tweaks for approval (never auto-apply)?
2. **Journal widget priority:** ship the read path as chat/perception only first, or also a cockpit "decision journal" widget in this round?
3. **`never_filled` proposals:** journal every proposal (incl. rejected/expired that never traded — richest for learning "what I declined and dodged"), or only ones that executed? *(Recommend: journal everything — the declines are signal.)*
4. **MODIFY path:** worth adding now, or keep approve/reject-only?
