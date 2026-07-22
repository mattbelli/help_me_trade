# Fusion Round 4 — Attention Tiers + the Flow Hot Lane (2026-07-22)

**Status: BUILT 2026-07-22** (tests green; verify-in-live pending — lane is EMPTY until ideas arm). Implements `FUSION_HORIZON_REDESIGN_2026-07-21.md` §2.6
(attention allocation) extended by two founder directives (07-22, recorded in BUILD_LOG):
(1) *"when an idea engine is triggered — that should automatically send that ticker into the
options flow hot lane"*; (2) *"not only are we watching for an entry — we watch while the trade
is active"* — (2) is the **position-watch round that follows this one**; this round builds the
attention substrate it depends on.

## Principle — attention follows the trade lifecycle

Perception resources are allocated by where the agent/user has skin, not by a static list:

```
open positions  >  triggered/armed ideas  >  warming episodes  >  baseline universe
```

The founder watchlist is OUTSIDE this ladder: it is an explicit human ask, always served,
never evicted by engine promotion.

## Code reality the design sits on (verified 07-22)

- Both flow lanes derive their underlying universe from the injected `watchlist_symbols()`
  callable, re-read EVERY cycle (`tools/flow_collector.py` re-reads watchlists state per call;
  `OptionsFlowRunner` docstring: no restart needed). The promotion seam is a merge at that
  callable — nothing else moves.
- `signals/options_flow/scan.py eligible_underlyings()` caps at `OPTIONS_FLOW_MAX_UNDERLYINGS`
  (warns + clips the tail). Priority ordering of the input list IS the eviction policy.
- Tier state already exists in the DB: `fusion_episodes.state`/masses (warming),
  `idea_outcomes.lifecycle_state` (`armed`/`trigger_fired`/`live`), positions in the ledger.
  No new state machine — tiers are a DERIVED VIEW.

## Tier model (derived, never stored as truth)

| Tier | Who | Resource it buys |
|---|---|---|
| T1 live | open-position symbols; ideas `armed`/`trigger_fired`/`live` | **flow hot lane** (OPRA subs via the seam below) + existing ws lanes |
| T2 poll | open episodes with thesis gross ≥ noise floor | already universally tape-covered; no new spend |
| T3 scan | everything else | nightly universe scan (exists) |

Derivation = one pure function `attention_tier(symbol_state) -> tier` reading episodes +
idea lifecycle + positions; computed on read (Desk/radar) and by the hot-lane writer.

## The flow hot lane (founder directive 1)

- **New table `flow_hot_lane`**: `symbol PK, reason ('position'|'idea_armed'|'idea_triggered'),
  promoted_at, last_qualified_at, demote_after_sessions` (dial, default 3). DERIVED-ish but
  persisted because hysteresis needs memory; rebuildable except the timestamps.
- **Writer**: the existing 60s lifecycle sweep (`signals/fusion/lifecycle.py`) upserts
  qualifying symbols (armed/trigger_fired/live ideas; open positions), stamping
  `last_qualified_at` and setting `reason` to the CURRENT strongest truth — reason weakens
  when the stronger state ends (Opus review 07-22: rank is the cap-eviction order; a stale
  strong rank would misorder eviction forever). Membership hysteresis lives entirely in
  demotion: no qualifying state for `demote_after_sessions` trading days → row deleted
  (promote instantly, demote slowly, never flap).
- **Reader**: collector's `watchlist_symbols()` becomes `founder watchlist ∪ hot lane`,
  ordered: watchlist first (never evicted), then hot lane by ladder rank, then — under the
  `OPTIONS_FLOW_MAX_UNDERLYINGS` cap — `eligible_underlyings` clips the tail exactly as today
  (warning names what was dropped).
- **Promotion date = that symbol's flow-baseline floor.** A promoted name's options-flow
  history starts at promotion; `promoted_at` is the per-symbol stationarity latch for the
  Stage-2 flow component (tape baseline is already universal). Never back-compare across it.
- ASTS proof case: tape → idea triggers → hot lane sub → options angle joins the dossier.

## Desk/radar surfacing

Radar rows gain a `tier` field; Desk shows it as a plain lowercase colored word (never a
pill — founder rule) beside contested. No new zones; "needs you" stays reserved for the
position-watch round.

## Explicitly out of this round

Position-watch divergence alerts (entry snapshot + reversal/continuation checks → Desk
"needs you"; phone rides the Round-5 push-restore decision). Equity-tape promotion (tape is
already universal). Any change to arming/gating (Round 5, founder T pick).

## Build slice

1. `db/fusion.py`: `flow_hot_lane` table.
2. `signals/fusion/attention.py`: `attention_tier()` + `sweep_hot_lane()` (pure + one writer).
3. Lifecycle sweep calls `sweep_hot_lane` (same 60s job — no new loop).
4. `tools/flow_collector.py`: merge hot lane into `watchlist_symbols()` (ladder order).
5. `/api/fusion/radar` + Desk: `tier` field + word.
6. Dial: `FUSION_HOT_LANE_DEMOTE_SESSIONS = 3` (+ validate_config assert).
7. Tests: tier derivation, promote-on-arm, hysteresis demotion, collector merge order under
   cap pressure, watchlist never evicted.

## Dials (founder-owned, changes as diffs)

`FUSION_HOT_LANE_DEMOTE_SESSIONS` (3) — quiet sessions before a promoted name drops out.
`OPTIONS_FLOW_MAX_UNDERLYINGS` (existing) — total cap; the ladder decides who feels it.
