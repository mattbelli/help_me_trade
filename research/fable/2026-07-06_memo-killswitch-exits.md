# DECISION MEMO — Should the kill switch block protective exits? (safety M2)
*Fable 5 · founder decision memo · 2026-07-06 · one page, one recommendation. Code refs verified today.*

## The question
When the kill switch is armed, `ExecutionService.submit()` rejects **every** order unconditionally
(`execution_service.py:57-74`, plus the RiskEngine gate at `risk_engine.py:66`) — including the exit
engine's protective sells (`exit_engine.py:348`, a plain reduce-only market sell). The emergency stop
freezes your ability to *shed* risk, not just add it.

## Why this got sharper since the audit
The daily-loss breaker's hard stage **auto-arms the kill switch**
(`risk_engine.py:426` → `apply_settings_patch({"trading": {"kill_switch_active": True}})`).
So the freeze is no longer only a deliberate human act: on a big losing day the system *automatically*
enters the state where app-managed stops/targets/time-exits stop firing. Exits here are app-managed —
there are no broker-resident stop orders — so frozen exit engine = genuinely unprotected positions.
The failure compounds exactly when it hurts: breaker trips because you're bleeding → exits freeze →
overnight gap risk on the very positions that caused the trip.

## The two failure modes being traded off
- **Freeze exits too (today):** worst case is *unbounded* — unmanaged longs through a gap/crash,
  no stop, until a human notices the critical push and manually disarms (disarm requires the admin
  token — correct for security, slow in a fire).
- **Allow exits while halted:** worst case is *bounded* — the system, possibly running on bad data,
  liquidates held longs at ugly prices. It can never go past flat: the sell path already enforces
  reduce-only (`RiskCheckResult` flags any sell that opens/extends a short), per-order caps still run.
  Maximum damage = an untimely full flatten; you end in cash, not short, not levered.

A bounded bad outcome beats an unbounded one. But "the system is insane, stop trusting even its
sells" is a real scenario — the
answer shouldn't be to delete the full freeze, just to stop *auto-arming* it. (Bad ticks could
spuriously stop-out everything at a flash-crash bottom — that's what the manual freeze is for.)

## Options
**A. Status quo** — kill switch = total freeze, breaker auto-arms it. Simplest mental model; the
compounding failure above is live. Reject.

**B. Kill switch allows reduce-only exits** — one state, exits exempt. Fixes the compounding
failure but destroys the "off means OFF" property of the big red button; there are moments you want
a switch that guarantees *zero* orders (suspected compromise, broker mess, data insanity). Reject.

**C. Two states (recommended):**
- **HALT** (`entries_halted`, new): blocks every exposure-increasing order (buys, short-opens);
  reduce-only exits keep flowing through the full RiskEngine. **The breaker's hard stage arms HALT
  instead of the kill switch.** Also manually armable — it becomes the default "something's wrong"
  lever. Critical push on arm, phone shows which state.
- **KILL SWITCH** (unchanged): total freeze, human-armed only, asymmetric auth as today. When armed
  with open managed positions, page critically with the count: "N positions now unmanaged."

## Recommendation
**Option C.** It keeps the sacred chokepoint semantics untouched (kill switch code path unchanged —
smallest possible diff to safety-critical code), gives the breaker a state whose failure mode is
bounded, and preserves a true master-off for the day you don't trust anything. The reduce-only
machinery it depends on already exists and is tested. Implementation is: one new settings flag, one
gate in `RiskEngine.validate` ordered before buying-power, breaker line 426 retargeted, phone/UI
badge. Adversarial review mandatory (touches `trading/`).

**Your call needed on:** (1) approve Option C; (2) should HALT also pause the *momentum rebalance*
(it both buys and sells — recommendation: yes, pause entirely; a rebalance is not a protective exit);
(3) disarm auth for HALT — normal token (symmetric, it's less dangerous than the kill switch) or
admin like the kill switch (recommendation: normal token to disarm, admin not needed).
