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
| `IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md` | Phase 1 implementation spec — DEPLOYED 07-16; audit gaps fixed in Round 0 (07-18) |
| `FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md` | ★ v3 BUILD spec for the fusion layer — the Phase 2 delta set ON the Unified Design (grill + 4 audits). Roadmap §4 item 2 builds to THIS. §2 scoring/lifecycle now has a proposed delta: see the Horizon Redesign |
| `FUSION_HORIZON_REDESIGN_2026-07-21.md` | Converged direction from 3 independent passes (orchestrator + zero-context Fable + zero-context Grok): decayed directional masses + conviction ratio replace day-buckets/binary contested; swing-only product lane; armed→triggered→expired lifecycle — DIRECTION BLESSED 07-21; supersedes C6-as-specced |
| `FUSION_LIFECYCLE_ROUND3_SPEC_2026-07-22.md` | Round-3 implementation spec (episode open/close lifecycle + armed/trigger/TTL idea states + expired/invalidated grading) — BUILT 07-22; ⚠ read the header's post-review amendments first (two-axis CAS guards, price-invalidation-only cascade, persisted rehydration seed — they override the body) |
| `FUSION_SCORING_NORMALIZATION_2026-07-22.md` | ★ SETTLED (3-pass converged): ranking = z = \|net\|/√(gross+prior); arming = DUAL GATE (z ≥ T AND conviction ≥ bar); Stage 2 = per-symbol baseline percentile (necessary, self-populating). Builds right after Round 3 |
| `AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md` | External gpt-5.6-sol technical audit (17 findings, 8/8 verified) — findings age as fixes land; Round 0 fixed the shipped-code set |
| `AUDIT_GPT56_PRODUCT_DIRECTION_2026-07-17.md` | External gpt-5.6-sol product-direction audit vs the founder vision — sequencing + Strategy Packs + blind spots |
| `BACKEND_COMPOSITION_AUDIT_2026-07-18.md` | Zero-context audit of the hmt-backend process (inventory, failure domains, split ranks) — all claims verified; step-1 fixes shipped; Rank-1 worker + Rank-2 exit cadence OPEN |
| `RESEARCH_WORKER_DESIGN_2026-07-18.md` | Rank-1 design: extract the 5 nightly research jobs into `hmt-research-worker` via the collector seam — APPROVED 07-19; R1a shipped, R1b after the Mon 07-20 probe verdict (board `rank1-research-worker-design`) |
| `CLASSIFICATION_LAB_PREREG_2026-07-16.md` | ★ Pre-registered print-lab metrics — LAW: never improvise metrics on the data |
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
