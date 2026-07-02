# help_me_trade — Staff Review: State & Release Strategy
*Fable 5 audit · read-only · 2026-07-02 · main branch only (did NOT see the backtesting research branch — see follow-up audit).*
*Note: two figures (a real account balance + the four account last-4s) redacted here vs the raw output, per the report's own P0.1 — don't let finances proliferate into more tracked docs.*

**Scale:** ~77.5k LOC Python, ~39.8k TS/TSX, + a native Expo app (58 TS files). 435 commits in 17 days. 144 test files / **1,145 backend test functions**, **0 frontend tests**. CI = ruff + pytest + `tsc`.

## 1. Overall state — honest maturity read
**Far more mature than a 17-day repo should be — but lopsided.** The safety spine + data/ledger layer are production-grade for one user; the Decision Engine (stated headline value) is honest scaffolding with no *paper-proven* edge yet (see follow-up backtesting audit — backtested alpha exists on another branch); the frontend is a large, fast-moving, untested surface.

**Genuinely strong:**
- **The chokepoint is real.** `aplace_order` has exactly one production caller (`execution_service.py:142`). `submit()` writes a `pending` audit row before any branch, re-checks kill-switch, hard-blocks live, funnels every rejection to `order_executions`. `RiskEngine.validate()` is fail-closed, ~11 named checks, Decimal throughout, with unusually good *why*-comments. Deny-by-default applied consistently. Best part of the codebase.
- **Honesty discipline.** Repeatedly demotes its own numbers (FIFO → `approximate_fifo`; scorer metadata says "edgeless, n=447"). Rare and correct.
- **Backend safety core is the best-tested part** — correct shape.

**Load-bearing but fragile:**
- **`api/main.py` is a 2,760-line god-file running 16 hand-rolled asyncio loops** in `lifespan`. A scheduler embedded in a web process; a dead loop stays dead. Biggest architectural fragility.
- **Decision Engine is infra without *paper-proven* edge.**
- **Frontend: 39.8k LOC, 27 widgets, zero tests.** The money-adjacent approve path has no automated check.
- **Broker layer is Alpaca-coupled in practice:** `RiskEngine` does `isinstance(broker, AlpacaAdapter)` (risk_engine.py:125,165); `ExecutionService` catches `AlpacaError` by name. "Multi-broker" is marketing, not architecture.

**Safety soundness — three real gaps:** (a) **no daily-loss / drawdown circuit breaker** — caps order count/value/BP/concentration but nothing stops bleeding the daily budget across many valid trades (CLAUDE.md promises this for bounded-auto — build it first). (b) `count_executions_today` is check-then-act (concurrent `submit()` can both pass). (c) **Auth is a single optional static token** — unset = fully open, and the same token that reads the watchlist can PUT settings that loosen caps / disarm the kill switch. Fine on a tailnet; first thing to change for release.

## 2. Cleanup — prioritized
**P0 (cheap, do first):**
1. **Personal finances in tracked docs** (HANDOFF balance `$[REDACTED]` / last-4s `••[REDACTED]` / P&L, throughout history). Repo is private today — so decide: "forever-private", or start keeping figures out of tracked docs.
2. **Inert mem0 vector search** — `ai/memories/__init__.py:75–98` iterates `.search()` results as a list; mem0 returns `{"results":[...]}`, so it silently falls back to token-overlap. Since R4 plans sqlite-vec, delete the mem0 path + drop `mem0ai`. Same scrutiny for `langgraph`/`langchain` (one file).
3. **Daily-loss circuit breaker** (docs already claim it).

**P1 (this month):**
4. **Retire the legacy page tree** (`app/{agents,charts,performance,…}`) + the still-registered monolithic `performance` widget (registry:143) now that it's split.
5. **Split `api/main.py`** — extract the 16 loops into a `jobs/` module with a tiny supervisor (restart-on-crash + heartbeat on `/health`). ~100 lines buys observability.
6. **Versioned migrations** — replace `try/except ALTER TABLE`-on-init across ~35 db modules with a `schema_version` table.
7. **Burn down CODE_AUDIT Tier 2** (spot-checked, accurate). Also: root `package.json` is `{}` (delete); a stray `hmt.db` sits in root vs `data/trading.db`.

**P2:** notification `kind` taxonomy; float-money in display dataclasses (blocks any non-Alpaca exec adapter); `References/` sprawl (60+ files — archive dead briefs); pin deps (`requirements.txt` all `>=` → adopt `pyproject.toml` + `uv.lock`).

## 3. What could be better
- **Job runner over ad-hoc loops** (supervisor + SQLite "last success" per job — don't need DBOS).
- **Testing is inverted:** add *one* Playwright test for the approval flow (paper proposal → pending card → approve → assert `order_executions` row). Skip broad FE coverage.
- **LLM observability:** pull Langfuse (R4) forward — it's the eval harness the Decision Engine needs, on the critical path, not a nice-to-have.
- **Machine-enforce the safety convention:** CI guard failing any PR touching `trading/` or `db/order_executions.py` without a `SAFETY-REVIEWED:` trailer.
- **`docker-compose.yml`** (backend + frontend + `data/` volume) — half a day, doubles as the release story.
- Frontend widget system is already past required polish — stop investing there.

## 4. What we're doing wrong — direct
1. **The commit stream contradicts the mission.** CLAUDE.md says deprioritize manual-trader chart polish; the last 10 days shipped a 12-brief charts overhaul (drawing tools!), theme systems, terminal restyles, 7 perf widgets, ⓘ-hover iterations — while the scorer stays edgeless-on-paper and fusion/learning work sits queued. "You built drawing tools on charts for an app whose thesis is the user shouldn't be staring at charts." Classic solo-founder comfort loop: UI ships dopamine daily; edge research fails silently for weeks. Give the Decision Engine a protected time budget.
2. **Safety-by-process accumulating where safety-by-code belongs.** 16 caught bugs is also evidence the pipeline *generates* ~16 near-safety-bugs; catch-rate depends on remembering to review. Encode invariants (CI guard + property tests on RiskEngine).
3. **No daily-loss guardrail** (the next autonomy rung is literally defined by it).
4. **Single point of failure:** serv + one SQLite file + one founder. Verify the backup actually *restores*.
5. **Auth default-open.**

## 5. Release strategy
**Uncomfortable premise:** what you'd release today is the plumbing (commodity), because the valuable part (a validated, calibrated Decision Engine) isn't paper-proven yet. OSS retail-algo plumbing is a red ocean (backtrader, Lumibot, Nautilus, Lean, freqtrade, OpenBB + a wave of LLM "AI trader" repos). Releasing now enters with your weakest asset and gives away your strongest future asset's framework before it exists.

**Open source? Not now, probably never the whole app.** Real finances in history; aggressively single-tenant (`USER_ID="trader"` hardcoded in `ai/memories/__init__.py:18`, personal labels, settings.json config); an order-placing agent wired to strangers' live accounts makes a solo dad the free support desk + moral lightning rod for their losses; velocity depends on not managing a community. The one real pro-OSS argument (money-code earns trust by being inspectable) applies to ~1,500 lines, not 117k.

**Broker strategy: go Alpaca-only for execution — you already are.** Only exec adapter is Alpaca; RiskEngine type-checks for it; Schwab is a personal read-only island. A true abstraction needs portable tradability, order-lifecycle mapping, error taxonomy, fill polling, Decimal-clean types — weeks, zero users demanding it, and every live adapter multiplies the safety-review surface of your most dangerous code. Alpaca is *the* dev-standard for this audience. Cheap seam-formalization only: add `symbol_exists`/`option_contract_exists` + a broker-agnostic error base to the `BrokerAdapter` Protocol so the chokepoint stops name-checking `AlpacaAdapter` — a day, keeps the door open. Keep Schwab as a clearly-labeled read-only importer.

**Recommended path:**
1. **Now → edge-proven: stay closed, build the Decision Engine.** No release work has positive EV until a strategy survives backtest→paper with honest calibration.
2. **First external milestone: a 3–5 person private beta, not a repo.** `docker compose up` + `.env.example` + Alpaca-paper-only defaults + mandatory `API_TOKEN` + de-personalized onboarding. Forces the right de-hardcoding (founder-tenant → any-tenant) without multi-tenancy/billing/hosting.
3. **If edge is real + beta retains: open-core.** Fresh-cut a *small* repo — chokepoint, RiskEngine, proposal/approval lifecycle, Alpaca adapter — as "the auditable safety core for LLM trading agents." Keep closed: Decision Engine, data integrations, cockpit, mobile. Premium = the edge + hosted convenience. BUILD.md's "commit artifacts, not data" split is already your open-core boundary.
4. **Gates to releasable (in order):** (i) mandatory auth + per-user secrets out of settings.json; (ii) de-personalize (labels, `USER_ID`, References scrub or fresh-cut); (iii) Docker deploy; (iv) daily-loss guardrail + Playwright approval test; (v) paper-only hard default with the live gate physically absent from the OSS cut.

**Bottom line:** the real strategic asset is the discipline (chokepoint, honesty, audit-first) + the not-yet-*paper*-proven edge. Protect founder time for the edge, keep execution Alpaca-only behind a formalized seam, stay closed until a strategy is validated, then open-core the small trustworthy spine and sell the brain.
