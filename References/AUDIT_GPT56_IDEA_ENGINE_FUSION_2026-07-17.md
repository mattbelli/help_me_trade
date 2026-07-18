# GPT-5.6 Sol external audit — Idea Engine (design + shipped Phase 1) + Fusion Evidence Bus spec (2026-07-17)

*Zero-context read-only audit dispatched via the Cursor bridge (model `gpt-5.6-sol`, 677s run).
Brief: `tools/composer-bridge/briefs/audit-idea-engine-fusion-2026-07-17.md`. Verification status:
8/8 claims spot-checked against code by the orchestrator were TRUE verbatim (mobile bare win%,
Path A register-result discard, canary chain, TEXT PKs, unused median config, git mode bit,
long-only alpha benchmark, weekday-only calendar). Triage: bucket A (shipped-code fixes) precedes
Phase A; bucket B (fusion spec revision) follows. Historical record — findings age as fixes land.*

---
## Ranked findings

1. **Critical — the shipped external dead-man failed its first scheduled production run and is not reproducibly executable.**  
Evidence — cron invokes the script directly (`tools/idea_engine_deadman.sh:38-39`); production logged `Permission denied` (`data/deadman.log:1`). Git records mode `100644`, although the working copy was manually chmod’d to `775`. The July-16 canary receipt arrived at 23:51 ET, seven hours after the 16:45 deadline.  
Consequence — a fresh checkout/deploy silently removes the only out-of-process page path; backend or push-pipeline death can remain invisible.

2. **Critical — Phase 1’s highest-volume existing lane never enters the ledger or lane meters.**  
Evidence — Phase 1 promises nightly scan, convergence and Path A registration (`References/IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md:8-11,164-168`). Convergence only notifies and increments in-memory return counters (`signals/options_flow/convergence.py:552-604`); repository search finds no convergence `register_idea` call. Live SQL: July 16/17 had 879 + 856 convergence notifications, but `idea_outcomes WHERE origin='convergence' = 0` and `lane_stats WHERE lane='convergence' = 0`.  
Consequence — 1,735 actionable cards generated no calibration samples, dispositions or silence alarms; “calibration counts everything” is false in production.

3. **Critical — a green canary proves neither ledger registration nor tracker operation.**  
Evidence — required chain is register → tracker sweep → push receipt (`References/IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md:181-185`). Code ignores `register_idea`’s return, catches registration errors, and immediately pushes (`signals/pipeline_audit.py:267-306`); receipt recording depends only on push-send count (`signals/pipeline_audit.py:308-314`). Live SQL shows both synthetic canaries still `pending` while both have `sent` receipts.  
Consequence — ledger or grader failure can coexist with a healthy canary and dead-man.

4. **Critical — the shipped phone surface still displays an unqualified bare win percentage and ignores “unproven.”**  
Evidence — Phase 1 requires `calibration="unproven"` rendering and inseparable probability qualifiers (`References/IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md:200-204`). Backend sends `ciLow` and `nSamples` (`signals/push.py:637-646`), but the mobile state declares only `winProb` and no calibration/CI/sample fields (`mobile/widgets/trade-proposal.tsx:19-30`), rendering bare `NN% win` (`mobile/widgets/trade-proposal.tsx:39-40,95-98`).  
Consequence — an earned or malformed probability reaches the user stripped of its uncertainty and sample size; sub-gate cards do not display the mandated honesty label.

5. **Critical — Fusion’s daily-bar approximation has pre-trigger temporal leakage, not uniformly conservative bias.**  
Evidence — Fusion claims daily grading is conservative and ordinally fair (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:183-190`). For an intraday idea, `_first_evaluable_date` returns that same date (`research/idea_tracker.py:69-91`), then the grader reads the entire daily high/low (`research/idea_tracker.py:265-320`), including movement before the trigger. Unified explicitly calls daily bars wrong for event-driven ideas (`References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:123-127`).  
Consequence — pre-trigger stops, entries or targets can determine outcomes; bias varies by trigger time and signature, invalidating the proposed score-decile and component comparisons.

6. **Critical — the Phase 2 integer-cursor transport has no stable integer cursor in either named event store.**  
Evidence — both governing documents require integer-ID polling (`References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:34,40`; `References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:35-43,67-73`). Both stores expose `TEXT PRIMARY KEY` IDs and upsert them (`db/options_flow.py:33-57,138-200`; `db/equity_tape.py:31-46,73-107`). Existing readers poll timestamps (`db/options_flow.py:266-268`; `db/equity_tape.py:152-154`).  
Consequence — engineers must invent hidden-`rowid`, timestamp or schema-migration semantics; replay, cursor advancement and conservation will differ between implementations.

7. **High — Fusion cannot construct its frozen plan from the persisted trigger facts.**  
Evidence — registration requires the first directional dot’s underlying price plus prior-close ATR (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:167-173`). Options events contain no underlying price or ATR (`db/options_flow.py:33-57`); current ATR acquisition makes an external daily-bar call (`signals/options_flow/convergence.py:423-429`), while intake forbids external fetches during bursts (`References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:75-77`).  
Consequence — implementations will use different delayed quotes/ATR values or violate the no-fetch topology, corrupting the frozen estimand.

8. **High — both Path A and nightly scan can report registration success when no ledger row was inserted.**  
Evidence — `register_idea` returns `None` for validation rejection or dedup (`db/idea_outcomes.py:492-515,612-614`), but Path A discards the result and increments `registered` (`trading/proposal_intake.py:170-190`). Nightly scan catches registration failure and still returns completed (`ai/research/setup_scan.py:1161-1203`); the job counts report ideas and announces “tracked in idea outcomes” (`api/main.py:1764-1779,1803-1817`). The required `idea_outcome_id` payload field does not exist outside the spec.  
Consequence — dropped or deduped samples look healthy, and proposal lifecycle linkage depends on a best-effort JSON lookup rather than the promised durable ID.

9. **High — shipped silence alarms are neither median-based nor capable of detecting a lane that never worked.**  
Evidence — spec requires trailing ten-trading-day median comparisons (`References/IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md:170-180`). `ALARM_RATE_MEDIAN_DAYS` is unused (`config/idea_engine.py:29-35`); code uses fixed thresholds (`signals/pipeline_audit.py:77-156`). Zero-output rules are disabled until previous nonzero output exists (`signals/pipeline_audit.py:61-71,100-105,119-124`), and there is no convergence rule.  
Consequence — a newly dead/miswired lane remains permanently exempt; production convergence demonstrates this exact blind spot.

10. **High — Fusion’s “market shocks wash out in alpha” claim is mathematically false, especially for shorts.**  
Evidence — claim at `References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:273-282`. Idea return is direction-signed, SPY remains long, and alpha is `idea_return - spy_return` (`research/idea_tracker.py:153-183,540-544`).  
Consequence — during a 10% market fall, a beta-one short can show roughly +20% “alpha,” doubling the common shock rather than removing it; shock slicing and lane comparison become misleading.

11. **High — Fusion’s principal five-minute decorrelation rule is non-implementable as written.**  
Evidence — options and equity observations must collapse into one participation observation (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:113-117`), but no receiving group, base, value units, cap or opposing-direction rule is defined. Options direction also needs a four-way right×execution-side mapping (`signals/options_flow/opening_pressure.py:125-136`), absent from the spec’s `bull|bear|none` contract.  
Consequence — two engineers will produce different stack scores, contested states and long/short registrations.

12. **High — the “top 25%, capped at 50” inbox policy degrades into first-50-by-arrival.**  
Evidence — cards emit immediately on threshold crossing (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:223-249`), with no eviction/replacement policy. `group_key` is not unique and notification creation always inserts a UUID row (`db/notifications.py:19-44,98-141`).  
Consequence — early marginal stacks consume the cap before stronger later stacks; concurrent updates can create duplicate cards rather than update in place.

13. **High — grading uses weekdays instead of exchange sessions and evaluates price events before expiry.**  
Evidence — `_is_trading_day` is only `weekday() < 5` (`research/idea_tracker.py:94-122`). Pending and triggered loops test zone/stop/target before checking expiry (`research/idea_tracker.py:268-328,388-434`).  
Consequence — holidays mature horizons early, while missing expiry-session bars can let a post-TTL bar trigger or resolve an idea instead of expiring it.

14. **High — Fusion’s request-volume estimate is arithmetically wrong and assumes an unbuilt grader.**  
Evidence — 2,000–3,000 symbols at 200/request is 10–15 requests, not 13–18 (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:175-182`). Current grading fetches one symbol per row (`research/idea_tracker.py:547-560,804-817`); no research grader uses `get_stock_bars_batch`.  
Consequence — implementing the specified population atop the current sweep creates thousands of calls per pass, not a dozen daily requests.

15. **High — RVOL-ex-prints compares unlike populations.**  
Evidence — Fusion subtracts current detected prints but retains the existing profile expectation (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:118-122`). Existing RVOL denominator is historical total volume including prints (`signals/options_flow/convergence.py:400-417`).  
Consequence — the residual is systematically understated and cannot be interpreted as an independent crowd signal without residualizing historical profiles too.

16. **Medium — Fusion destroys the REASON A/B identity in its proposed origin names.**  
Evidence — Unified requires separate `reason_v1`, `reason_v2` and hash-sampled comparison (`References/IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md:85-88`); Fusion replaces origin with `fusion_llm_<horizon>` (`References/FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md:191-198`).  
Consequence — model version and horizon become conflated or one dimension disappears, preventing the specified matched v1/v2 calibration.

17. **Medium — alarm dedup records success even when page delivery fails.**  
Evidence — `notify` swallows persistence/push errors (`signals/notify.py:76-119`); `_fire_alarm` ignores its result and always records `alarm_fired` (`signals/pipeline_audit.py:211-233`).  
Consequence — a failed warning or critical push is suppressed for the rest of the day.

## Verdicts

- `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md` — **FIX-FIRST:** stable cursor contract and a canary that actually traverses the claimed pipeline.
- `IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md` — **FIX-FIRST:** findings 1–4, 8–9, 13 and 17; its stated acceptance gate has not passed.
- `FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md` — **RETHINK:** transport, frozen-plan inputs, grading estimand, decorrelation semantics and card selection are currently incompatible or underdefined.
