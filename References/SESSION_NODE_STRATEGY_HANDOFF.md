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
- `detect_node_triggers(intraday_bars, nodes, *, rearm_points=2.0, mode="reversion", max_per_node=2,
  max_triggers_per_day=6) -> list[NodeTrigger]` — entry side. Reversion direction (support node → long,
  resistance node → short, **target = POC**; POC is target-only, never an entry node) with **re-arm
  hysteresis** (fire on touch → DISARM → RE-ARM only after price moves ≥ `rearm_points` away → chop doesn't
  spam, round-trips re-fire) + per-node and per-day caps. Defaults: rearm 2.0 SPY (≈20 ES), 2/node, 6/day
  (tunable). `NodeTrigger`: `ts, node_name, node_price, direction, target_price, touch_price, context`.
- **EXIT side (v2):** `detect_exit_prompts(position: OpenPosition, bars_since_entry, *, invalidation_buffer=0.5)
  -> list[ExitPrompt]` — models Adam's Set management as prompts. `ExitPrompt.kind` ∈ {`target_t1` (reached
  POC → trim ~half, stop to BE), `runner_extend` (past POC to the opposite value edge → trail the runner /
  T2), `invalidation` (a CLOSE back through the entry node → consider flat), `day1_close` (last RTH bar →
  bank day-1 or carry a swing)}. Each kind fires once; deterministic from (position + post-entry bars).
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
5. **Exit prompts (v2):** when a node proposal is APPROVED and a SPY position is open, build an `OpenPosition`
   (entry ts/price/direction/node + the day's `nodes`) and call `detect_exit_prompts` each scan; dedup by
   `(position_id, kind)` (deterministic, fire-once). Push each new prompt as a **phone management notification**
   ("Reached POC — Set trims half", "runner extending — trail", "closed through entry node — consider flat",
   "RTH close — bank or swing"). These are PROMPTS — the human still taps to act; a "reduce" routes through the
   same sell-only-reduces-held-long chokepoint. No auto-exit.
6. **LEARN**: log every proposal + exit prompt + outcome (live analog of `data/research/setstudy_log.csv`) so we
   can refine which nodes/contexts get taken and pay.

## Safety checklist (please confirm in your adversarial review)
- Propose-only: no path auto-executes; human approves on phone. ✓ (reuse R3 approve path)
- Paper only; LIVE stays hard-blocked. ✓
- Rule force-created DISABLED. ✓
- No new broker path — SPY through `ExecutionService.submit` only. ✓
- No fabricated win%/edge on the proposal — honest low-confidence structure tag. ✓

## Separate task for serv — durable Schwab token sync to the PC research box
serv now runs the auto-refresher and rotated the Schwab token today, which **invalidated the PC research
machine's copy** (Schwab rotates on refresh). Today it was hand-fixed with a one-time `scp` from serv. **Make
it durable:** have serv's auto-refresher (or a tiny post-refresh hook) **push the fresh `data/schwab_token.json`
to the Windows research box over Tailscale** each cycle (same channel as the nightly backup), so the PC always
has a current token and never refreshes it itself. Keep serv the SOLE owner/refresher of the Schwab token; the
PC is read-only consumer (research uses it for free recent SPY/`/ES` pulls). This is infra, independent of the
strat above — but it's the fix for the "PC token goes stale" problem and worth doing while you're in here.
(Note: futures-history deep pulls use Databento, not Schwab — unaffected.)

## Coordination
This is your lane; I stay in `research/`. When my branch lands you'll have `session_node_signal.py` (entry +
exit-prompt logic). Ping me if the interface needs adjusting for the scan loop. Start DISABLED; the user arms it
when he wants to run the daily app-test.
