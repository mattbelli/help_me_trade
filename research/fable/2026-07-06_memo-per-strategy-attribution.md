# DECISION MEMO — Per-strategy attribution: many strategies, one account (caps + P&L)
*Fable 5 · founder decision memo · 2026-07-06 · one page, one recommendation. Code refs verified today.*

## The question
You want several strategies running in ONE account, each independently capped, measured, and
compared — with only 3 paper accounts and, eventually, one real account holding the whole
70/20/10 combined book. Today every risk cap is account-wide (`max_position_pct`,
`max_daily_notional_usd`, `max_daily_loss_usd` … `config/settings.py:101-106`) and no P&L view
groups by strategy.

## What already exists (more than the card implies)
`strategy_id` is threaded end-to-end through the *attribution* layer: `managed_positions`
(NOT NULL, unique on `account_id+symbol+strategy_id` — two strategies can already hold the same
symbol in the same account as separate ledger rows), `order_executions`, `fill_attribution`
(safety-M1 quarantines any fill that can't be attributed), `decision_journal`, exit engine,
proposal intake. **The recording half of per-strategy attribution is done and adversarially
reviewed.** What's missing is exactly two consumers: a per-strategy P&L/exposure *view*, and
per-strategy *budgets* in the RiskEngine (which today never sees a strategy_id — `OrderIntent`
doesn't carry one).

## Options
**A. One-strategy-per-account discipline (status quo).** True isolation, zero code. But it caps
you at 3 concurrent paper strategies, and it does NOT match the destination: the validated
combined book is 3 sleeves in one account by design. You'd be paper-testing an architecture you
won't run live. Reject as the long-term answer.

**B. Virtual sub-books (recommended).** Strategies share the account; per-strategy budgets are
enforced by the RiskEngine; P&L attribution uses the ledger rows that already exist. The account-
wide caps stay as the outer belt (concentration/notional against the merged broker position),
per-strategy budgets become an inner gate.

**C. Defer everything to promotion.** Cheapest today, but you lose the paper-phase evidence
("which sleeve is earning its risk?") that the whole trust ladder runs on.

## Recommendation: B, split into two phases with different urgency
- **Phase 1 — Measure (build soon, it's cheap and read-only):** a per-strategy P&L/exposure view
  over `managed_positions` + `order_executions` (realized per closed managed position, open
  exposure, win rate, per-sleeve calibration tie-in to the decision journal). No RiskEngine
  change, no execution-path risk, immediate value while you judge scan ideas and sleeves.
- **Phase 2 — Enforce (build at combined-book promotion, not before):** add `strategy_id` to
  `OrderIntent`; new Settings→Strategies budget fields (per-strategy max open notional, max
  daily loss); one new RiskEngine gate ordered with the existing caps. Ship it in the same
  adversarially-reviewed round as the momentum promotion + alert batching (both already agreed
  to land at promotion). Enforcement before there's real money at stake is speculative code on
  the sacred path — exactly what the engineering discipline says not to write early.

## Your call needed on
1. Approve the B / two-phase split (Phase 1 soon, Phase 2 at promotion).
2. Budget denomination for Phase 2: **$ notional per strategy (recommended — matches the
   existing cap style and the breaker)** vs % of account equity.
3. Same-symbol-across-strategies: the ledger supports it; keep it allowed (recommended, the
   combined book needs it — momentum + dip_buy will both touch large caps) or forbid via a
   proposal-intake dedup?
