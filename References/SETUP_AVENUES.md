# Setup Avenues — how the LLM gets more (honest) setups to recommend

*2026-07-02 · PC/orchestrator → server agent. Context: first live tests of "give me your best
3 setups" (DeepSeek V4 pro + flash) returned earnings_drift every time. This doc explains why
that's structural, the two-tier design that lets the LLM recommend beyond backtested
strategies without fabricating numbers, and the ranked plan for more avenues. Companion:
`research/fable/2026-07-02_RESEARCH-BRANCH_backtesting-deep-dive.md`.*

## 1. Why every slate is earnings_drift (structural, not a bug)

earnings_drift is the only strategy in the book with signals on a random Tuesday:

| Strategy | Signal frequency | Typical state |
|---|---|---|
| earnings_drift | ~400 setups/yr (calendar-driven) | firing constantly |
| dip_buy (±S/R) | ~16–20/yr; needs stock −5%/3d AND SPY −1.5%/3d | silent outside pullbacks |
| steep-YC dip | same + YC > 0.5 gate | **gate closed since 2022** — zero by design |
| vix_spike | ~0.6 episodes/yr | dormant (needs VIX>28 from calm) |
| residual momentum | monthly rebalance list | "fires" once a month, not plumbed as setups |
| session_node | intraday /ES-SPY node touches, propose-only | narrow, one instrument |

Ask for 3 setups from a menu with one item in stock → 3 of that item. Both models converging
on earnings_drift is the system behaving **honestly**.

**Guardrail:** do NOT fix this by prompting the LLM to diversify its slate. Forced variety
when one edge is live = fabricated conviction — the exact failure the mission forbids. Three
earnings-drift cards on a calm day is the correct answer. Fix supply, not the narrator.

## 2. Does a setup need a backtested strategy? No — it needs a tier label

**Tier A — strategy-backed.** Matches a validated entry condition → carries backtested
win%/EV/CI from `strategy_stats.json`. Phone-approvable, fully sized. (Today's only tier.)

**Tier B — thesis trades.** LLM fuses perception (news, sector RS, levels, breadth,
catalysts) into a setup no backtest covers. Legitimate, with two hard rules:
1. Card ships `win_prob = None` + explicit label "no calibrated stats — thesis trade".
   Never a number the code didn't produce.
2. Smaller size cap than Tier A.

The crucial mechanic: **every Tier B card goes through the decision journal.** Realized
outcomes accumulate against the LLM's implicit judgment; after a few dozen journaled thesis
trades, `compute_calibration` tells us what its discretion is worth — and if it's good,
Tier B *earns* a calibrated base rate of its own. session_node already runs this exact
pattern (propose-only, human-managed, discretion study). The DecisionCard rule makes it safe:
deterministic code owns every number; the LLM can rank, shrink, veto, narrate — never inflate.

## 3. More avenues, ranked

1. **Perception before strategies.** The agent has no screener/top-of-funnel, can't see
   regime, news, or breadth — all computed, all invisible (fable perception audit, Tier-1
   scopes). An LLM whose only candidate source is the earnings calendar will recommend
   earnings trades forever. This does more for slate diversity than any new strategy.
2. **Make dormancy visible.** Slate should say *"dip_buy: 0 candidates (no qualifying
   pullbacks); steep_yc: gated closed (YC=0.4); vix_spike: dormant (VIX 14)"* instead of
   silence. Free, regime-aware, trust-building — "nothing qualified today" is a feature.
3. **Un-knife-edge the gates.** YC>0.5 and VIX>28 are knife-edges sitting exactly where 2026
   is flapping them (deep-dive §2). Ensemble versions (YC 0.3–0.7 blend, VIX 26–30) are both
   more robust AND partially open in today's regime instead of binary-dead.
4. **Surface residual momentum as setups.** 70% of the book produces a ranked 10–20 name
   target list monthly — plumb the rebalance as a monthly card set (enter/exit/rank), not
   just a portfolio action.
5. **New strategies selected for frequency + trigger orthogonality, then edge-hunted.** The
   book is "rare macro events + one calendar strategy." The gap is other *event classes*
   with steady flow: insider cluster buys (EDGAR Form 4, free), pre-earnings drift (banked),
   PEAD×steep-YC (banked), buyback+momentum (banked), sector RS rotation (data on disk).
   Frequency is a criterion for which hypotheses get trials — never a reason to lower the
   validation bar (`/edge-hunt` gauntlet applies in full).
6. **Long-run avenue multiplier: the ML scorer** (`model.json`/`ev_table.json` path) —
   screener → features → calibrated win% on ANY candidate when `sufficient=true`, no named
   sleeve required. Blocked until the z-score leakage fix (deep-dive §1.1) — its calibration
   is currently inflated.

## 4. Notes for interpreting early tests

- n=3 prompts is far too small to conclude anything about slate distribution.
- pro-vs-flash disagreeing on *ranking* is expected and fine — fusion/ranking is the LLM's
  assigned job, as long as every number on the card traces to code.
- Track slate composition over weeks in the journal; judge avenues by journaled outcomes,
  not by variety.
