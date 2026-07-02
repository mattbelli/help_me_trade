# Fable audits

Deep read-only strategic/technical reviews by Fable 5 subagents. Each is a point-in-time
snapshot grounded in code (file refs throughout). Reviews are candid and opinionated by design.

| Date | Audit | One-liner |
|---|---|---|
| 2026-07-02 | [State & release](2026-07-02_state-and-release.md) | Maturity read + cleanup backlog + release strategy → stay closed until edge-proven, execution Alpaca-only, later open-core the small safety spine. |
| 2026-07-02 | [Backtesting & strategy](2026-07-02_backtesting-strategy.md) | The edge is REAL (combined book ~+5–6%/yr Jensen α at β0.76, PBO=0.000) — but its proof lives in gitignored scratch (not CI-reproducible); trust in *months* via fidelity + break-detection, not re-proving significance. |
| 2026-07-02 | [LLM integration](2026-07-02_llm-integration.md) | The trading partner is built-but-**unplugged** (dead journal injection, disabled memory extraction, inert mem0, 3 divergent chat loops); items 1–4 are wiring not research. |
| 2026-07-02 | [Safety & auto-execution](2026-07-02_safety-execution.md) | Chokepoint is airtight, but **CRITICAL C1**: `execution_mode=paper` doesn't guarantee a paper endpoint → a live order can fire while labeled paper; **H2** auth is optional+unscoped (same token can disarm the kill switch via `PUT /api/settings`); no daily-loss breaker (H1); snapshot-TOCTOU caps (C2). Fix C1/H2/H1/C2 before any human-tap-free execution. |
| 2026-07-02 | [Data & numbers correctness](2026-07-02_data-correctness.md) | Ledger core + firewall are solid; risk is in scope-resolution + LLM-context assembly. **C1** the LLM's perf context blends paper+real as the "real track record" (firewall breach, live); **C2** paper-snapshot unrealized on real lots; **C3** the account resolver silently falls back to all-real; **H6** `_find_ev_bucket` fabricates EV from the top bucket into phone proposals (5-line fix, mission-critical). |
| 2026-07-02 | [Mobile phone-approval](2026-07-02_mobile-approval.md) | The approve→execute spine is airtight (atomic claim, idempotent, honest); the *step-away shell* isn't there — **S1** biometrics are unverifiable client theater (a static token = the whole money loop); **R1** fire-and-forget delivery (no retry/fallback/audit, defaults to APNs sandbox); **R2** Live Activity can never update/end (stale cards w/ live buttons forever); **R3** missed proposals vanish silently; **H3** no fill-report loop. APNs key not committed. |
| 2026-07-02 | [Ops & reliability](2026-07-02_ops-reliability.md) | The 16 loops are actually well-guarded; the danger is *around* them. **CRIT-1** the Schwab token watchdog is structurally blind + reauth cron already failed once → risk of a **multi-day silent Schwab outage** the moment daily restarts stop; **CRIT-2** backups never leave the box + restore never tested; **HIGH-3** DB grows unbounded (96→502 MB in 12 days, no prune); **HIGH-4** `/health` covers 1 of 16 loops, nothing consumes it (silence == success). |
| 2026-07-02 | [Perception completeness](2026-07-02_perception-completeness.md) | A well-built microscope, a missing wide-angle lens. Per-symbol depth is strong, but the agent can't **find** candidates (no screener → "an engine with no top-of-funnel"), can't see **regime** (FRED exists, invisible), can't read **news** (Finnhub wired for UI, hidden), can't see its own **calibration**. ~80% of the fix is "one registry line per feed." Also: `open_orders` silently dropped (bug); `propose_rule` arms immediately (misnamed). |
| 2026-07-02 | [★ RESEARCH BRANCH: backtesting deep-dive](2026-07-02_RESEARCH-BRANCH_backtesting-deep-dive.md) | Follow-up focused on `research/*.py` + `_edgehunt` + setstudy. Two active biases inflate the ML track (full-sample z-score leakage in `dataset_io.py`, same-bar-close fills); DSR/PBO exist but aren't wired (`n_trials` untracked); regime-*stratified* reporting yes, shorter windows no; exits stay dumb (our own kills prove it); setstudy never reads the 7GB of MBO parquets it was built around (N=19 honest forward triggers, not 137). Execution order: H6+C1 → pipeline honesty fixes → commit-the-book → setstudy MBO rebuild. |
| 2026-07-02 | [Test & eval harness](2026-07-02_test-eval-harness.md) | The safety core has some of the best example-based tests in a solo project; but **~0 of 1,145 tests measure whether the agent's decisions are good**, and the founder-facing approval UI is untested. Highest-ROI: golden net-cash reconciliation, RiskEngine/firewall property tests (Hypothesis), CI trailer-guard + pinned deps, ONE Playwright approval spec. **The big gap: no LLM eval harness** (trace table → 30–50-case golden set → journal-calibration report). "The app can prove it will never place a forbidden order, and cannot prove a single one of its ideas is worth taking." |

*Note: the state & release doc has a real balance + account last-4s redacted, and the data-correctness doc has two position figures redacted (per the state doc's own P0 — keep finances out of tracked docs).*

---

## ★ Roadmap (the forward plan)

- [**Decision Engine roadmap**](2026-07-02_decision-engine-roadmap.md) — *From grounded Q&A bot to an autonomous risk/reward recommendation engine.* Builds on the 9 audits. The engine's organs already exist and disconnected; connect them under one `DecisionCard` contract (deterministic code owns every number, the LLM owns fusion/narrative/ranking and can only shrink/veto, never inflate). Wide data ingested by **edge-per-effort** (regime/breadth/catalysts/news = wiring; fundamentals/insider = cheap gated adds; social hype = inverted risk-flag only; options-flow/analyst-ratings = deferred/decoration; **$0/mo new spend until the journal proves lift**). A **trade-vs-invest** differentiator (4 modes; ship swing+position, invest is the new build, day is an honest shell). Proactive daily **Slate Pass** → phone. **7 phases (crawl→walk→run)** — Phase 0 (3–5 days) wires what exists and makes it feel exceptional immediately; new data + autonomy come *after* the eval/journal loop.

## Recurring cross-audit themes (the throughline)

The same shape appears in all nine: **the moat is discipline; the gaps are concentrated, small, and mostly wiring/verification — not capability.**

**What's genuinely strong (stop worrying about these):** the execution chokepoint (airtight, no side door, Schwab fenced 4 ways); honesty enforced *in code* not just prompts (anti-fabrication stripping, scorer returns `None`, net-cash-is-truth); a **real backtested edge** (combined book ~+5–6%/yr Jensen α, PBO=0.000 — survived a genuine gauntlet); and some of the best example-based safety tests in a solo project.

**Signals confirmed by MULTIPLE audits (highest confidence → do first):**
1. **The single unscoped static token** — flagged by Safety (H2), Mobile (S1), and adjacent in Data. Same token reads the watchlist and can disarm the kill switch / approve orders. *The #1 structural hardening before any live money.*
2. **No eval harness** — Test/eval + LLM audits: the app can't tell if its decisions are good; nothing would notice a 30%-misroute regression.
3. **REASON has nothing to fuse** — LLM ("it narrates, doesn't reason") + Perception ("no screener/regime/news; ~80% of the fix is one registry line per feed").
4. **Can't be left alone yet** — Ops (blind Schwab token watchdog, off-box backup never wired) + Mobile (no delivery guarantee, no fill-report loop) — the literal thesis of the app.

**The cheap-but-critical cluster (days, not weeks):** H6 stop-fabricating-EV (5 lines); un-blend paper+real in LLM context (C1); Schwab watchdog honesty (Ops CRIT-1); the `open_orders` perception bug + `propose_rule` rename; CI trailer-guard + pinned deps; switch on the journal/memory injection + kill inert mem0; wire the perception Tier-1 scopes.

**Needs a founder decision:** the daily-loss breaker number + halt-vs-block behavior; the auth scope-split design; commit-the-book (the #1 research move); whether to build the mobile reliability shell now.
