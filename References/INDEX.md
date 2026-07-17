# References/ — doc map

*What's CURRENT vs historical. Rule (hook-enforced): adding a new `References/*.md` requires updating this
file in the same commit. When a doc is superseded, move its line to Historical AND stamp a
`> **SUPERSEDED BY [x]**` line at the top of the dead file. Weekly freshness audit checks this map.*

## Orientation & ops (start here)

| Doc | What it is |
|---|---|
| `HANDOFF.md` | "You are here" — current focus. Read first every session. Keep lean; old versions → `HANDOFF_ARCHIVE.md` |
| `BUILD.md` | Roadmap + dated history rows (400KB — **grep, never read whole**) |
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
| `IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md` | Phase 1 implementation spec — DEPLOYED 07-16; verify gate + acceptance drills live here |
| `CLASSIFICATION_LAB_PREREG_2026-07-16.md` | ★ Pre-registered print-lab metrics — LAW: never improvise metrics on the data |
| `COLLECTOR_PRESSURE_AUDIT_2026-07-16.md` | The "DO NOT MERGE" gauge audit — deferred findings F3/F4/F8 still open |
| `DECISION_ENGINE_FUSION_DESIGN_2026-07-13.md` | Current Decision Engine fusion/scoring layer design |
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
| `MEMORY_OVERHAUL_DESIGN.md` | Copilot memory overhaul — PARKED in founder lane, do not build unguided |
| `ALPHA_PREP_SPEC.md` | Single-founder → alpha-testable/cloud plan (directional, parked) |
| `DATA_LAYER_LIMITS_AUDIT_2026-07-14.md` | Alpaca/system rate limits audit |
| `SHORT_TRADING_FINDINGS.md` | Decision record: no agent-traded shorts (measurement stays short-aware) |
| `FLOW_INTELLIGENCE_DESIGN_2026-07-11.md` | Flow capture/schema/display design (mostly built; still the reference) |

## Historical (kept for the record — do not treat as current)

- **Built design records / completed briefs**: `DECISION_JOURNAL_DESIGN.md`, `NOTIFICATIONS_FOUNDATION.md`,
  `COPILOT_TERMINAL_DESIGN.md`, `AUTO_ENTRY_SPEC.md`, `TACTICAL_SLEEVES_SPEC.md`,
  `MOMENTUM_PORTFOLIO_SPEC.md`, `REGIME_SLEEVES_IMPL.md`, `FLOW_DTE_DIVERSITY_SPEC_2026-07-11.md`,
  `FLOW_VIZ_DESIGN_2026-07-11.md`, `MOBILE_CHAT_BRIEF.md`, `WEB_CHAT_DESIGN_BRIEF.md`, `R3_KICKOFF.md`,
  `UI_AUDIT.md`, `UI_POLISH.md`, `MEMORY_SYSTEM_REPORT.md` (superseded), `CONSOLIDATION_PLAN.md`,
  `PHASE_6_BRIEF.md` + 9 `PHASE_6_*_BRIEF.md` batch briefs, `SESSION_HANDOFF.md` (superseded),
  `IDEA_LEDGER_UNIFICATION_SPEC_2026-07-11.md` + `IDEA_LEDGER_SPEC_2026-07-15.md` (both superseded)
- **Recon snapshots (point-in-time, 06-29/30)**: `CONSOLIDATION_RECON.md`, `CONSOLIDATION_S2_RECON.md`,
  `CONSOLIDATION_S3_RECON.md`, `CHAT_RESTYLE_RECON.md`, `THEME_OVERHAUL_RECON.md`, `THEME_PORT_RECON.md`,
  `SAVE_LAYOUTS_RECON.md`, `PERF_SPLIT_RECON.md`, `PHASE_1B_EMIT_RECON.md`, `PHASE_1C_FRONTEND_RECON.md`
- **Cross-machine relay mail (serv↔Mac↔PC, all resolved)**: `MAC_*.md`, `SERV_*.md` (except
  `SERVER_SETUP.md`/`SERV` none current), `RN_*.md` (except `RN_SKIA_CHART_RELAY.md` above),
  `PC_TO_SERV_2026-07-09_options-flow.md`, `PC_DECISION_ENGINE_QUESTIONNAIRE.md`,
  `SERV_TO_MAC_HANDOFF_2026-07-03.md`
- **Research saves**: `RVOL_VOLUME_PROFILE_RESEARCH.md`, `SCHWAB_MARKETDATA_RESEARCH.md`,
  `FLOW_CAPACITY_RESEARCH_2026-07-11.md`
- **Mockups / dumps**: `hermes-ui-v2.html`, `hermes-console-draft.html`, `index.html`, `index(1).html`,
  `flow_notif_mockup.html`, `flow_viz_design.html`, `current orchestrator convo.md`
