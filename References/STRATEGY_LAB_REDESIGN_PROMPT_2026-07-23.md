# Strategy Lab redesign — zero-context designer brief (2026-07-23)

*Founder-requested prompt for fresh-eyes HTML mockups. Hand this WHOLE document to a
context-free agent (no repo access, no screenshots of the current UI — that's the point).
Run 2–3 agents in parallel; variant instruction at the bottom differs per agent.*

---

## The prompt

You are designing one page — the **Strategy Lab** — for a personal autonomous-trading
cockpit. Produce a single self-contained HTML file (inline CSS/JS, no external assets, no
frameworks) with realistic fake data, desktop-first at ~1440px. It is a **mockup**: static
data hardcoded in the page, but interactive where cheap (tab switches, expanding rows).
Do not ask questions; make the calls and annotate notable design decisions in HTML comments.

### The product in one paragraph

This app is an autonomous trading agent for one user (eventually multi-user): it watches
markets in the background, proposes trades to the user's phone for approve/reject, and —
within hard guardrails — will eventually execute on its own. The cockpit is where the user
**configures and supervises the agents and reviews what they did** — it is NOT a manual
trading terminal. The Strategy Lab is the page where every trading strategy and every
AI-managed portfolio lives: what's running, what it did, whether it's any good, and the
evidence behind those judgments.

### What must be displayable (the content inventory)

Two families of actors, one visual grammar, clearly typed:

**1. Mechanical strategies** — deterministic scanners with fixed rules. Examples:
"Earnings Drift" (enters 10 trading days before earnings, exits before the print),
"Residual-Momentum Portfolio" (monthly S&P-500 rebalance, 10 slots × 10%), "Session-Node
Reversion (SPY)", plus several disabled/experimental ones. Each has: enabled/disabled,
entry mode (propose = human approves each trade / auto = executes within caps), bound
account, caps (max concurrent positions, max deployed $), universe, and a lifecycle state
(live / validation-pending / parked).

**2. LLM-managed books** — AI portfolio managers, each on its own $100k paper account:
- "Deep Seek" — a cheap single model.
- "Fable" — a frontier single agent.
- "Claude Panel" — a multi-agent panel: bull and bear advocates cross-examining the same
  candidates, macro + social context seats, a portfolio/risk seat, and a judge that scores
  a fixed rubric and issues the final decisions. Its nightly output is a REPORT (bull case,
  bear attack per candidate, judge verdict with reasoning) plus proposed orders.
- "Matt + LLM" — human-directed: the USER decides, the AI implements and writes a nightly
  snapshot; it never proposes on its own.

**Per strategy/book, the lab must be able to show:**
- Performance: equity curve, realized P&L (net-cash), open positions with live P&L,
  alpha vs SPY, drawdown. Paper vs (future) live money clearly firewalled.
- **Idea ledger grades**: every idea any actor generates is recorded and outcome-graded
  whether it was taken or not. Win-rates are computed per lane and are only shown when
  calibrated (statistically bounded); uncalibrated lanes show grades/counts, never a
  headline win%. For the Panel: per-seat grading (did the bear's vetoes dodge losers? does
  the judge beat its own inputs?).
- Proposal history: what it asked for, approved/rejected, outcome.
- For LLM books: the nightly report as a first-class readable artifact.
- Controls: arm/disarm (arming is always an explicit human act), entry mode, caps editing,
  and a visible kill-switch state.

**3. Backtesting** — the evidence layer:
- Configure + run a backtest (strategy, universe, date range, params) and a queue of runs.
- Results: equity curve vs benchmark, drawdown, per-trade table, summary stats,
  train/validation split, pre-registered kill lines (e.g. "−30% = strategy is dead").
- A **validation queue**: candidate strategies awaiting backtests before they may go live.
- Honesty rule baked into the UI: backtest LEVELS are approximate (fills/costs are
  simulated) — RANKINGS and relative results are the trustworthy part; the UI must label
  this rather than present backtest P&L as achievable truth. Unvalidated signals are
  labeled as perception, never dressed as validated edge.

**4. Live look-in (the "right now" layer)** — across ALL accounts (mechanical + LLM books
+ the human-directed one): current positions, pending proposals awaiting the phone, today's
fills, daily P&L per book, and which agents ran today (with links into their artifacts).

### Design language (constraint card)

Dark, data-dense, terminal-modernist: monospace numerals, tabular alignment, flat surfaces
(no cards-nested-in-cards, no drop-shadow candy), restrained color where ONLY direction/
side/P&L gets saturated hues, categorical tags are plain lowercase colored words — never
pill boxes. Typography does the hierarchy, not chrome. Density is a feature: this user
reads tables. Numbers the system cannot honestly claim (uncalibrated win%, simulated
levels) are visibly hedged — honesty labels are part of the design, not fine print.

### What good looks like

The user opens the page and in ten seconds knows: what's armed, what each actor did today,
who's making money, who's on probation, and what's waiting on him. In two clicks he can
read WHY any actor did what it did (nightly report, idea grades, backtest evidence).
Deliver: an overview (the roster), a strategy detail view, an LLM-book detail view (Panel,
with its debate report), and a backtest view — as tabs or drill-ins within the one file.

### Variant instruction (differs per agent)

- **Variant A (this agent):** obey the design-language card strictly.
- **Variant B (this agent):** obey the content inventory, but IGNORE the design-language
  card — bring your own visual system; best ideas win.
- **Variant C (this agent):** obey the design-language card; additionally optimize the
  page to also work at phone width (390px) with a bottom-tab layout — show both widths.

---

*Output location when run by the orchestrator: `References/strategy_lab_mockups/variant_<x>.html`.*
