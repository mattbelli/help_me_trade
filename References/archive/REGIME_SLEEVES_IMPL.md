# REGIME_SLEEVES_IMPL — implementation note (serv build)

*2026-07-03. Implements `REGIME_SLEEVES_SPEC.md` (spec lives on
`research/decision-engine-data-layer`). Branch: `feat/regime-sleeves`.*

## What shipped
- **`signals/regime_sleeves.py`** — both sleeves on their own post-close loop
  (`api.main._regime_sleeves_loop`, ~10-min cadence, self-gated to 16:05 ET → midnight,
  deduped per trading day). Signals evaluate COMPLETED daily bars; proposals target
  next-open entry (phone approval overnight). Propose-only; nothing here executes.
- **generals_dip** — monthly point-in-time top-30 by 60d avg dollar volume (computed
  from the production market-data feed as of the PRIOR month-end; ETF denylist),
  close>SMA50 AND 3d ≤ −3.5%, ranked most-oversold first, max 5 concurrent. Exit rides
  the EXISTING exit engine: `trading/exit_engine._strategy_kind` maps the id to the dip
  evaluator and the seeded None stop/target/trail legs leave a pure 10td time cap
  (steepyc_dip pattern) — no trading/ changes.
- **vol_event** — SPY-only, triggers `drawdown_stab` + `term_flip`. **Deliberate
  deviation from the spec:** the validated `vix_spike` sleeve was NOT rewritten/absorbed;
  it stays a separate strategy and is counted read-only toward the shared **cluster cap
  of 1**. A trigger firing while capped is journaled `suppressed` with its trigger_id
  (the side-by-side comparison data). Exits are managed by the sleeve module itself
  (exit engine skips unknown kinds) with the same `pending_exit` marker format, so
  closure reconcile + decision-journal outcomes resolve unchanged.
- **vix3m** — FRED VXVCLS via the keyless CSV endpoint, stored as macro series `vix3m`
  in the same regime refresh (refreshes even without FRED_API_KEY). Stale >3 trading
  days ⇒ `term_flip` goes dormant (visible status, no error).
- **Kill lines (mechanical)** — generals: month-end check, trailing-6m mean excess vs
  SPY < 0 OR trailing-60-trade win rate < 45% (win-rate leg needs the full 60-trade
  window). vol_event: per-episode — MAE ≤ −15% or 3 consecutive losses disables
  drawdown_stab; mean 21d return < +2.0% over 10 episodes suspends term_flip.
  Auto-disable flips the sleeve's `enabled` flag / the trigger's params flag via
  `db.strategies.update_strategy` (the existing mechanism), journals EVERY check
  (pass or fail), and notifies.
- **Tier-B labeling** — `signals/strategy_stats.REGIME_LOCAL_CONVICTION`: win_prob=None
  + the committed `regime_stats` block (with caveats, verbatim). Checked BEFORE
  strategy_stats.json so a mistaken artifact entry can never override it. These sleeves
  are never written into strategy_stats.json.
- **Ships disabled** — both seeded DISABLED + UNBOUND, entry_mode=exit_mode=propose
  (session-node pattern); the API blocks flipping either to auto.
- **Dormancy visibility** — `GET /api/strategies/regime-sleeves/status`: per-sleeve
  last-scan reason ("generals_dip: 0 flushes in top-30 today", "vol_event: no active
  vol episode; term_flip dormant (vix3m stale)"), universe month/symbols, trigger
  flags, latest kill-check. No UI yet — API payload only.

## Known limitations (documented, accepted)
- FRED VIX/VIX3M publish with ~1-day lag → vol_event triggers can fire one session
  late vs the backtest (same lag the existing vix_spike sleeve carries).
- Universe/signal bars come from the live market-data provider (the momentum daily-bar
  store has no volume column); point-in-time is enforced by the `as_of` cutoff.
- Kill-check excess uses journal fills vs SPY close-to-close over the same window
  (holiday-naive trading-day math, as elsewhere).
