# Short trading — research findings & decision record (2026-06-21)

**For the `serv` agent / future sessions. TL;DR: research does NOT support agent-traded shorts. The
no-short safety invariant should STAY. Do not build a short execution path on the strength of this study.**

## Why this exists
The founder raised enabling short trading (mirror of the validated long dip-buy: *market + stock
**overbought** → sell short*, with a shorter hold). This records what the backtest found and the resulting
decision, so the intent isn't lost and isn't acted on from a rosy memory.

## What was tested (Windows / Norgate survivorship-free, 909 sym / 15y)
Short mirror via `research/strategy_*.py` `side="short"`: overbought stock (+5%/3d) + overbought market
(SPY +1.5%/3d) + below-SMA200, exit mirrored (stop above / target below / breakeven / runner-or-trail
down). Hold sweep, GROSS (no borrow), full 15y:

| hold | win% | EV gross |
|------|------|----------|
| 5d   | 49.3 | +0.213%  |
| 10d  | 49.1 | +0.096%  |
| 20d  | 49.1 | +0.045%  |

- **Shorter hold IS better for shorts** (mirror of longs) — confirms the drift-headwind intuition. But even
  the best (5-day) is weak.
- **Significance (best config, hold 5, GROSS):** per-trade +0.21%, but honest block-bootstrap 95% CI
  **[-0.07%, +0.37%]**, P(mean≤0) = 9.5% → **NOT significant**. (`python -m research.significance --db
  data/research/strategy_short.db`.)

## Why it fails (and what it tells us)
1. **No edge even gross.** The honest CI straddles zero. Not real.
2. **Negative after frictions.** Borrow on overbought small-caps ≈ 0.3–0.5%+ → flips +0.21% gross negative.
3. **Backtest is OPTIMISTICALLY biased for shorts** anyway: the simulator's −100% return floor understates
   unbounded squeeze losses, and daily-bar stops assume clean fills (real gap-ups blow through them).
4. **Diagnostic:** the LONG edge (+0.81% gross, significant, CI [+0.38,+1.02]) is ~4× the short and the
   short isn't significant → the long works mostly via the market's **upward drift (beta tailwind)** plus a
   small real mean-reversion kicker. The symmetric short can't survive the drift headwind.

## Decision
**KEEP the no-short invariant** (`trading/`: sells only reduce a held long; agent never gets a short path).
The research provides no edge to justify the largest risk-surface expansion in the app.

## IF short capability is ever revisited (NOT on this study's basis)
Only with a *separately validated* short edge AND all of the following — each is non-negotiable, and any
change to `trading/`/chokepoint gets **adversarial Sonnet review** before commit:
- Sell-to-open path through `execution_service.submit()` (today sells only reduce a held long).
- RiskEngine: margin checks, **borrow/shortability** check, **hard per-position loss cap** (unbounded
  downside), max aggregate short exposure, gap/squeeze guard.
- Paper-first on Alpaca; stays behind the LIVE double-gate (Increment 3).
- Re-run the short backtest with a realistic borrow-cost model and a squeeze-aware loss model before trusting
  any number.

Related: `References/HANDOFF.md` (session summary), `research/strategy_*.py`, `research/significance.py`.
