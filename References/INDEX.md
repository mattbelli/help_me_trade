# References/ — doc map

*What's CURRENT vs historical. Rule (hook-enforced): adding a new `References/*.md` requires updating this
file in the same commit. When a doc is superseded, move its line to Historical AND stamp a
`> **SUPERSEDED BY [x]**` line at the top of the dead file. Weekly freshness audit checks this map.*

## Orientation & ops (start here)

| Doc | What it is |
|---|---|
| `HANDOFF.md` | OPEN threads only (tense rule: entries die when closed; no archive — history = BUILD_LOG + git). Read first every session |
| `BUILD.md` | The plan: vision, ★ Decision Engine, roadmap, debt, principles, gates (~50KB, readable) |
| `BUILD_LOG.md` | Dated journal, one row per round, newest first (350KB — **grep, never read whole**) |
| `board.json` | Live kanban (rendered at `/board`) — in-flight work, founder decision lane |
| `ARCHITECTURE.md` | Plain-English subsystem tour |
| `ARCHITECTURE_DIAGRAM.excalidraw` / `ARCHITECTURE_MODULES.excalidraw` | Editable diagrams: concept-level agent loop / module+process level (2026-07-17) |
| `STARTUP.md` | serv access, tailnet addresses, service basics |
| `SERVER_SETUP.md` | The always-on Linux box: units, env, collectors |
| `AGENT_LOOP_RESEARCH.md` | The PERCEIVE→…→LEARN loop architecture + tech research |
| `IDEA_BANK.md` | Founder's product vision + the why — committed direction, not a wishlist |
| `PLATFORM_PARITY.md` | Web vs phone feature tracker (living) |
| `CODE_AUDIT.md` / `REFACTOR_BACKLOG_2026-07-08.md` | Cleanup backlogs (audit 06-20; maintainability pass 07-08, chokepoint excluded) |

## Active specs & governing designs

| Doc | Status |
|---|---|
| `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md` | ★ GOVERNING design for the idea engine (Paths A+B unified). Supersedes both idea-ledger specs |
| `MULTI_ANGLE_REASONING_AB_2026-07-19.md` | Design: bull/bear/judge panel A/B vs the single veto-grade voice — paired shadow, alternating control, alpha-adjudicated prereg metrics. Opus-reviewed (fixes folded); AWAITING FOUNDER GO |
| `LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md` | ★ BUILD spec for the 4 LLM/human paper books (Deep Seek/Fable/Claude Panel/Matt + LLM): nightly pack→session→propose→approve pipeline, `book:*` ledger lanes, panel seat design, shorts-unlock round, 6 increments. Settled 07-23 |
| `MOBILE_AGENT_BRIEF_2026-07-23.md` | Handoff brief for the Mac mobile-app agent: render `format:"markdown"` report bodies (the one required fix) + book-report notification/push shapes + 07-23 backend digest |
| `STRATEGY_LAB_REDESIGN_PROMPT_2026-07-23.md` | Self-contained zero-context designer brief for the Strategy Lab redesign (content inventory: mechanical strategies + 4 LLM books + backtesting + live look-in; honesty labels as design elements). Mockups land in `strategy_lab_mockups/` |
| `IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md` | Phase 1 implementation spec — DEPLOYED 07-16; audit gaps fixed in Round 0 (07-18) |
| `FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md` | ★ v3 BUILD spec for the fusion layer — the Phase 2 delta set ON the Unified Design (grill + 4 audits). Roadmap §4 item 2 builds to THIS. §2 scoring/lifecycle now has a proposed delta: see the Horizon Redesign |
| `FUSION_HORIZON_REDESIGN_2026-07-21.md` | Converged direction from 3 independent passes (orchestrator + zero-context Fable + zero-context Grok): decayed directional masses + conviction ratio replace day-buckets/binary contested; swing-only product lane; armed→triggered→expired lifecycle — DIRECTION BLESSED 07-21; supersedes C6-as-specced |
| `FUSION_LIFECYCLE_ROUND3_SPEC_2026-07-22.md` | Round-3 implementation spec (episode open/close lifecycle + armed/trigger/TTL idea states + expired/invalidated grading) — BUILT 07-22; ⚠ read the header's post-review amendments first (two-axis CAS guards, price-invalidation-only cascade, persisted rehydration seed — they override the body) |
| `FUSION_ATTENTION_TIERS_ROUND4_SPEC_2026-07-22.md` | Round-4 spec: tiers = derived view (positions > armed/triggered > warming > baseline; founder watchlist outside the ladder, never evicted) + `flow_hot_lane` promotion seam at the collector's `watchlist_symbols()` merge — BUILT 07-22 |
| `FUSION_SCORING_NORMALIZATION_2026-07-22.md` | ★ Stage 1 BUILT 07-22 (z ranking live on radar/Desk; arming = DUAL GATE z ≥ T AND conviction ≥ bar, wired at ARM with the founder T pick) + Stage-2 backfill harness (`fusion_z_baseline`, floor-latched 07-16). Read the as-built header first |
| `AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md` | External gpt-5.6-sol technical audit (17 findings, 8/8 verified) — findings age as fixes land; Round 0 fixed the shipped-code set |
| `AUDIT_GPT56_PRODUCT_DIRECTION_2026-07-17.md` | External gpt-5.6-sol product-direction audit vs the founder vision — sequencing + Strategy Packs + blind spots |
| `BACKEND_COMPOSITION_AUDIT_2026-07-18.md` | Zero-context audit of the hmt-backend process (inventory, failure domains, split ranks) — all claims verified; step-1 fixes shipped; Rank-1 worker + Rank-2 exit cadence OPEN |
| `RESEARCH_WORKER_DESIGN_2026-07-18.md` | Rank-1 design: extract the 5 nightly research jobs into `hmt-research-worker` via the collector seam — APPROVED 07-19; R1a shipped, R1b after the Mon 07-20 probe verdict (board `rank1-research-worker-design`) |
| `CLASSIFICATION_LAB_PREREG_2026-07-16.md` | ★ Pre-registered print-lab metrics — LAW: never improvise metrics on the data. ⚠️ Its central question (is the paid paired feed justified?) was ANSWERED 07-26 by subscribing — see the flow-baseline prereg below |
| `FLOW_BASELINE_PREREG_2026-07-26.md` | ★ Pre-registered multi-day per-name DIRECTIONAL flow baselines + change detection at 3–45d swing horizons, on the whole-market Databento `tcbbo` corpus. MFE/MAE/terminal in ATR units; train/validation/test split; mid-cap tier registered as a falsifiable sub-hypothesis. WRITTEN BEFORE DATA |
| `FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_A.md` | Dated addendum, pre-outcome: midpoint rule replaces strict at-the-quote as PRIMARY (57.4%→89.0% of prints, 42.4%→92.7% of premium — mid prints carry 57.5% of premium). ⚠️ Standing sell-skew must be characterized, not read as signal; tilt reported demeaned vs whole-market daily mean |
| `FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_B.md` | Dated addendum, pre-outcome: ONE spot source for historic AND live (prior-session split-adjusted close) — a mid-study reference change manufactures a change event on every name at the seam. Spot source is part of `schema_version`: changing it = full re-roll, never in-place. Index roots (30.8% of premium) stay `mny_bucket='unknown'` by DECISION, not oversight |
| `FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_C.md` | Dated addendum, pre-outcome: the DIRECTION DECISION RULE. `u` is unbounded ([-61,+90]), so 0.40% of prints carry 171.92% of `sum(u*premium)` — which direction a bucket "has" depends on the estimator. Registers three measures (net premium / clipped u / raw u), requires ALL THREE to agree, else `abstain`. ⚠️ Robustness checks here must be PREMIUM-weighted: disagreement is 0.24% of buckets but 15.8% of premium. Declares a 20–35%-of-premium abstention band on the ~258 unexamined sessions; rule chosen after seeing 2025-07-28, so that session is in-sample and cannot confirm it |
| `FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_D.md` | ★ Dated addendum, pre-outcome: **CORRECTS C** (C left in the record, not edited). ⚠️ The three measures are ONE signed axis at three weightings, NOT three votes — `sign(u)` equals the midpoint side by construction, so a 2-of-3 majority counts one opinion twice and is forbidden. Rule + 20–35% band UNCHANGED; abstains now carry a state (`no_opinion`/`weighting_unstable`/`contested`), reported by premium share and never pooled. ⚠️⚠️ MULTI-LEG: 12.96% of prints / 33.33% of premium, and excluding them FLIPS the session sign of all three measures — now a grain (`multileg` in the rollup PK, `schema_version=3`), both arms stored, neither presentable alone. OPRA `flags` marks nothing (constant 194); the `ts_event` collision is a heuristic and is registered as one |
| `FLOW_ROADMAP.md` | ★ CURRENT — the ordered plan from corpus to tradeable signal, founder-set order. Phase 0 live-lane liveness (parallel), 1 corpus integrity + 4 measurements + Addendum E, 2 open interest (free, no deadline), 3 equity matching / dealer-hedge footprint (Alpaca SIP ns trades, free), 4 market-regime GATE (not a blend). ⚠️ Do the phases in order; each feeds the next. End state = regime gate over name selection, because only **6.4 of 5,554 names are effectively independent** (verified) |
| `FLOW_ALPHA_RESEARCH_2026-07-26.md` | Multi-agent research round (6 Opus lenses + 6 critics + Fable synthesis) on where alpha is in the whole-market corpus. ⚠️ Carries an orchestrator VERIFICATION HEADER — 2 claims confirmed in code, 3 refuted; read it before trusting any figure. Headline: no vendor has ever published a forward test; the industry's at/above-ask construct REVERSES at our horizon (Pan & Poteshman); package direction is unrecoverable from prints alone; the registered kill rule is near-unfalsifiable |
| `FLOW_PACKAGE_ORIENTATION_DESIGN_2026-07-27.md` | PROPOSAL (awaiting founder review): the zero-context ideation round entered into the record before code. Headline = package orientation from the NET price's position in the per-leg synthetic band (attacks the net, which allocation cannot destroy — dodges the four-critic per-leg kill; UNVERIFIED, probe gated behind roadmap 1.9). Also: chat claims refuted by corpus facts (no MLET/TL\* condition codes in our tcbbo — `flags` constant 194), classifier frame w/ Cboe Open-Close labels, phase extensions (rolls, QCT, hedge-lag, empirical GEX, intraday lead-lag), and the Databento historic 10-min-block / 23–33 min sawtooth lag fact. Changes NO registered rule |
| `IV_RANK_BASELINE_PREREG_2026-07-25.md` | ★ Pre-registered VRP / IV-rank study — the gate on `options-income-lane`. LAW: thresholds and outcome-consequences fixed before data. RUN 07-25, verdict NO (BUILD_LOG) |
| `IV_RANK_BASELINE_PREREG_2026-07-25_ADDENDUM_A.md` | Dated addendum to the above: horizon recovery, the level-vs-rank control that killed the rank gate, conditional premise. SECONDARY arms |
| `IV_LEVEL_PREREG_2026-07-25.md` | ★ Study B, the IV-level axis (successor to the rank study). RUN 07-25: effect REAL (B1/B2/B3 pass) but B4 fails on our universe — breadth, not the phenomenon. ⚠️ POST-RUN NOTE: B5 was a miscalibrated test, do not reuse |
| `IV_HORIZON_BREADTH_PREREG_2026-07-25.md` | ★ Study C — the horizon × universe 2×2 at breadth. Fills the one cell Studies A/B left empty (wide universe × our own 21-session forward RV) and re-tests the level axis + the +0.0418 band at that horizon. ⚠️ C6 pre-registers a survivorship bias that flatters the result (a live bar API cannot serve a delisted ticker) |
| `OPTION_SPREAD_GATE_PREREG_2026-07-25.md` | ★ The spread gate — is Study C's +0.0445 reachable once you pay the bid-ask? Spread expressed in VOL POINTS so it compares directly. Saturday run says PROHIBITIVE (band 195% of the edge, 3.2× wider than our own names) ⚠️ but the prereg makes PROHIBITIVE **non-decisive** on closing quotes — intraday re-run REQUIRED before it is a finding |
| `COLLECTOR_PRESSURE_AUDIT_2026-07-16.md` | The "DO NOT MERGE" gauge audit — deferred findings F3/F4/F8 still open |
| `DECISION_ENGINE_FUSION_DESIGN_2026-07-13.md` | REASON scorecard→EV design — a LATER layer, NOT "the fusion layer" (that's the Evidence Bus spec); gated on factor admission + 8 founder decisions |
| `DECISION_ENGINE_RESEARCH.md` | Evidence base (06-21 deep research: fusion, calibration, LLM role) |
| `DECISION_ENGINE_HONESTY_REDTEAM_2026-07-08.md` | Honesty audit of every score-to-user path; founder chose SUPPRESS |
| `EARNINGS_DRIFT_SIZING_EXPERIMENT.md` / `EARNINGS_DRIFT_EXTRA_DAY_EXPERIMENT.md` | Active PC/Norgate experiment briefs (07-16) |
| `STRATEGY_VALIDATION_QUEUE_2026-07-08.md` | 3 edge candidates awaiting PC backtests (residual_momentum, turn_of_month, pead_confirmed) |
| `NORGATE_PULL.md` | PC runbook: survivorship-free training artifacts |
| `CHART_ENGINE.md` | ★ The native chart engine contract — read before touching `frontend/lib/chart-engine/` |
| `RN_SKIA_CHART_RELAY.md` | Phone chart sharing plan (Skia painter adapter — parked, still the plan) |
| `WATCHLIST_SPEC.md` | Watchlist v3 design (round 1 shipped 07-13; deferred columns still governed here) |
| `WATCHLIST_DEFAULTS_SPEC.md` | Ship-with-product default watchlists — SPEC, ready to build |
| `WORKSPACES_SPEC.md` | Workspaces + page library (v2 BUILT 07-14) |
| `THEME_TOKENS.md` | Theme knob model (editor rebuilt 07-12) |
| `PERCEPTION_LAYER.md` | Agent read layer design (get_context/query/manifest — built) |
| `RESEARCH_ENGINE_V2.md` | Research engine specialist fan-out design (built 07-03) |
| `MULTI_ACCOUNT_PLAN.md` | Durable multi-account architecture spec |
| `MEMORY_OVERHAUL_DESIGN.md` | Copilot memory overhaul — Phase 0+1 SHIPPED 07-06/07 (judge + authority model live in `ai/memories/`); Phase 2 retrieval pending (board: `memory-overhaul`) |
| `ALPHA_PREP_SPEC.md` | Single-founder → alpha-testable/cloud plan (directional, parked) |
| `DATA_LAYER_LIMITS_AUDIT_2026-07-14.md` | Alpaca/system rate limits audit |
| `SHORT_TRADING_FINDINGS.md` | Decision record: no agent-traded shorts (measurement stays short-aware) |
| `FLOW_INTELLIGENCE_DESIGN_2026-07-11.md` | Flow capture/schema/display design (mostly built; still the reference) |

## Historical (kept for the record — do not treat as current)

All historical docs live in **`archive/`** (moved 2026-07-18 — the root holds ONLY the current docs above).
`archive/README.md` is that folder's map: built design records + completed briefs, recon snapshots,
resolved cross-machine relay mail (MAC_/SERV_/RN_/PC_), research saves, mockups/dumps, and the pre-06-20
planning docs merged into BUILD.md. **Rule: when a root doc is superseded, stamp it
`> **SUPERSEDED BY [x]**`, `git mv` it to `archive/`, and move its INDEX line's content into
`archive/README.md` — in the same commit.** Never update an archived doc; change the living doc instead.
