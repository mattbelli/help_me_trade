# Handoff to the serv agent — wire the Session-Node Reversion strat (propose-only, app-test vehicle)

*From the research/PC agent (branch `research/decision-engine-data-layer`). This is the LIVE-wiring half —
your lane (signals/registry/`propose_trade`/UI). I build the detection logic; you wire propose→phone.*

## What this is (and why it's allowed past the edge-gate)
A **propose-only day-trading trigger** whose PRIMARY purpose is to **exercise the whole app end-to-end, daily**
(PERCEIVE → propose → phone Approve/Reject → chokepoint → **paper** execute → report → log). It is the app's
**autonomy-ladder rung 2** made concrete. It is **NOT** a claimed mechanical edge — we proved the mechanical
profile/level approach is beta. The design deliberately puts the **machine on PERCEIVE** and the **human on
DECIDE + EXIT**:
- The agent detects a node touch and **proposes** a trade to the phone. It **NEVER auto-executes.**
- The **human approves every trade** (his discretion — does it align with his @adaamset "Set" read? — is the
  filter) and **manages the exit manually** (v1).
- **Paper only.** Fixed small size.

Because it's **propose-only + human-approved + paper**, it does NOT need the edge-validation gate that
profile-MR is parked behind — it's a decision-aid + pipeline test, not an auto-executed edge. **Force-create
the rule DISABLED**; the human arms it (same as your existing R3-C trade-rule policy).

## Instrument (decided)
**SPY shares** (Alpaca-tradeable; SPY ≈ ES/10, same index). The user reads Adam's `$ES` Set for discretion,
but the executable leg is SPY through the **existing chokepoint** (RiskEngine → ExecutionService). **No new
broker path.** (Futures aren't on Alpaca — that's why SPY, not /ES.) **Compute the nodes on SPY directly**
(Alpaca SPY 1-min) so there's no ES↔SPY translation.

## What I deliver (research — call this, don't reimplement)
`research/session_node_signal.py` (committed on my branch; merges with the rest of the data layer):
- `compute_reference_nodes(prior_session_bars, *, n_bins=100, value_area_pct=0.70) -> ReferenceNodes`
  (POC/VAH/VAL + prior-day high/low from the prior RTH session). Reuses `build_volume_profile`.
- `detect_node_triggers(intraday_bars, nodes, *, rearm_points=1.0, mode="reversion") -> list[NodeTrigger]`
  — reversion direction (support node → long, resistance node → short, **target = POC**) with **re-arm
  hysteresis**: a node fires on touch then DISARMS, and only RE-ARMS after price moves ≥ `rearm_points` away
  (so chop at a level doesn't spam; a genuine round-trip re-fires). `rearm_points` default 1.0 SPY (≈10 ES).
- `NodeTrigger` carries: `ts, node_name, node_price, direction, target_price, touch_price, context`.
- Pure/no-lookahead/no `trading/` imports — safe to call from the scan loop.

## What you build (live wiring)
1. **Scan-loop integration** (during RTH): each scan, get SPY 1-min for the prior RTH session + today-so-far
   (Alpaca), `compute_reference_nodes(prior)` + `detect_node_triggers(today_so_far, nodes)`.
2. **Dedup**: the re-arm logic is **deterministic from the bar series**, so recompute each scan and dedup
   against already-proposed triggers by `(session_date, node_name, trigger_ts)` → never double-propose the
   same fire. (Stateless + deterministic — no armed-state to persist.)
3. **Propose** each NEW trigger through the existing **`propose_trade` intake seam** → `create_phone_proposal`:
   SPY, `direction`, fixed small notional/qty, entry = market (or marketable limit), an honest context string
   (`context` from the trigger) + a **"structure-reaction, low-confidence" flag** (calibrated, never
   fabricated — do not attach a fake win%). Respect the global pending cap + market-hours gate.
4. **Rule**: a `type:"trade"` signal-rule for SPY, **force-created DISABLED** (`create_rule` policy — human
   arms it). One position-direction at a time is fine; rely on the existing sell-only-reduces-held-long guard.
5. **Exit = human** (v1): the proposal is ENTRY-only; the user closes manually. (v2: exit prompts.)
6. **LEARN**: log every proposal + its outcome (live analog of `data/research/setstudy_log.csv`) so we can
   refine which nodes/contexts actually get taken and pay.

## Safety checklist (please confirm in your adversarial review)
- Propose-only: no path auto-executes; human approves on phone. ✓ (reuse R3 approve path)
- Paper only; LIVE stays hard-blocked. ✓
- Rule force-created DISABLED. ✓
- No new broker path — SPY through `ExecutionService.submit` only. ✓
- No fabricated win%/edge on the proposal — honest low-confidence structure tag. ✓

## Coordination
This is your lane; I stay in `research/`. When my branch lands you'll have `session_node_signal.py`. Ping me
if the interface needs adjusting for the scan loop. Start DISABLED; the user arms it when he wants to run the
daily app-test.
