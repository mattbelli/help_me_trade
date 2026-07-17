# Refactor Backlog — help_me_trade (maintainability audit, 2026-07-08)

*Every row below was confirmed real **and** safe by two independent reviewers against current file:line. This is a maintainability pass — it does **not** touch the execution chokepoint (`trading/execution_service.py`, `trading/risk_engine.py`), Decimal money handling, or the kill-switch/deny-by-default gates. All five lenses (god-modules, dead-stale, duplication, inefficiency, over-engineering) produced verified findings; none came back empty.*

---

## 1. Top wins (biggest first)

| # | Win | Where | Payoff |
|---|-----|-------|--------|
| 1 | **Split `api/main.py`** — the headline god module you named | `api/main.py` (3,403 LOC) | The hottest merge-conflict file in the repo. ~30 background-job loops + a bars cache + 54 inline HTTP routes in one file, even though **12 domain routers already exist** and prove the pattern. Split → `main.py` ~400-700, everything else in focused modules. |
| 2 | **Split 5 more god files** | `lib/api.ts` (1,480), `tv-light-chart.tsx` (1,841), `workspace-grid.tsx` (1,081), `execute_tool` in `tools.py` (~690), `performance-page.tsx` (806) | ~6,000 LOC of front-of-mind files broken into ~200-500 LOC modules. Pure code-moves behind barrels/registries; verified by `npm run typecheck` + chat/tool tests. |
| 3 | **Delete + dedup ~800 LOC** | orphaned mobile-synthesis path, 2 parallel watchlist APIs, ~15 copies of `formatMoney`, ~20 hand-rolled widget-fetch scaffolds, broker sync/async twins, `context.py` copy of main's RVOL helpers, `config/settings.py` triple-listed trading schema | Less surface to maintain, one source of truth per concern, drift eliminated. |
| 4 | **Kill 4 hot-path inefficiencies** | `ai_performance_context` (~7-8 ledger scans → 1), `get_all_latest_snapshots` N+1 (~6×N queries → constant), `init_ai_tables`/`init_db` re-running 18 no-op ALTERs on **every** memory/snapshot read, duplicate FIFO/recon compute | Faster LLM decision path + faster dashboard poll for near-zero risk (read-only analytics + idempotent DDL). |

**Bottom line:** ~770-820 LOC of genuine deletion/dedup, and the six largest files (~9,700 LOC today) collapse so nothing stays over ~1,000 LOC. The SPLITs are net-LOC-neutral (relocation) but are the real maintainability unlock.

---

## 2. Ranked backlog, grouped by action

### SPLIT (structural — relocate, no logic change)

| Impact | file:line | Problem | Concrete fix | loc_delta | Effort | Risk |
|---|---|---|---|---|---|---|
| ★ Headline | `api/main.py:232` & `:1570` | 3,403 LOC fusing ~30 `_*_loop`/`_*_once`/`_poll_*` background jobs + `_register_background_jobs` + lifespan (232–1453), a `_BARS_CACHE` (2722), and **54 inline `@app.*` routes** — despite 12 routers already wired via `include_router` (1547–1558). | Move the scheduler cluster → `api/background_jobs.py` (expose `register_background_jobs()`); bars cache + `/api/bars` → `api/bars_routes.py`; carve inline routes into `api/watchlist_routes.py` (watchlist+watchlists+price-alerts+drawings, ~19), `api/workspace_routes.py` (workspace+chart-layout+session-node, ~9), `api/market_routes.py` (snapshot/scanner/rvol/fundamentals), `api/system_routes.py` (settings/kill-switch/entries-halt — **auth guards verbatim**). `main.py` → app wiring + lifespan only. | 3,403 → ~400-700 main + ~6 modules | L | Moderate: shared module-level state (market hub, `_wl_cached_bars_batch`, `_schedule_profile_build`, poll caches) must move with its cluster; watch background-task registration order. **Do domain-by-domain in path-scoped commits, run the app once to confirm loops start.** Not the execution chokepoint. |
| High | `frontend/lib/api.ts:1` | 1,480 LOC defining ~60 types + ~50 fetchers for every domain (snapshot, bars, settings, watchlists, RVOL, Schwab, session-levels, reports, price-alerts, drawings, proposals, earnings, workspace) — imported by nearly every feature. | Convert to a `lib/api/` folder: shared core (`apiFetch`, auth headers, `API_BASE`, formatters) in `client.ts`; per-domain modules (`watchlists`, `market`, `settings`, `schwab`, `charting`, `reports`, `proposals`, `workspace`) re-exported through `index.ts` **barrel** so `from '@/lib/api'` sites don't change. | 1,480 → ~8 modules + barrel | M | Low — pure code-move behind a barrel; `npm run typecheck` verifies, no runtime change. |
| High | `frontend/components/trading/tv-light-chart.tsx:1` | Largest frontend file (1,841 LOC): 83 hooks in one component + pure geometry helpers (`timeToLogical`/`logicalToTime`/`distToSegment` @97-121), drawing hit-test/create flow, session levels, oscillator studies, **and two inline sub-components** (`ChartAlertContextMenu`@1630, `AlertCreatePopover`@1725). | Extract geometry → `chart/geometry.ts`; the two self-contained menus → `chart/alert-menus.tsx`; oscillator block (~1109-1224) → `useOscillatorSeries` hook; drawing state → `useChartDrawings` hook. Core wiring stays. | 1,841 → ~1,000 + 4 files | L | Moderate: shared chart/series refs across extracted hooks — verify in-browser (drawings/alerts/oscillator toggles) + typecheck. |
| Med | `frontend/components/workspace/workspace-grid.tsx:93` | 1,081 LOC mixing the grid/pan/layout engine with unrelated header chrome: `ChartHeaderControls`@93, `ChartHeaderMenus`@208, `WidgetHeader`@467, `WidgetRemoveButton`@383, `WatchlistHeaderControls`@436. | Extract header chrome → `workspace/headers/` (`chart-header-controls.tsx`, `widget-header.tsx`); `workspace-grid.tsx` keeps only `WidgetCard`/`WorkspacePageGrid`/pan helpers. Centralizing chrome also honors the *no-second-widget-headers* rule. | 1,081 → ~550 + headers ~530 | M | Low-moderate — self-contained components sharing store hooks; typecheck + in-browser. |
| Med | `ai/chat/tools.py:519` | `execute_tool` is a single ~690-LOC `if name == …` chain inlining all 20 chat tools — the file's god-function; every new tool grows it. | Replace with a `_TOOL_HANDLERS: dict[str, Callable]` registry; lift each block into a module-level async handler (`_tool_get_portfolio_snapshot`, …). `execute_tool` → ~15-line lookup+dispatch keeping the unknown-tool fallback. **`TOOL_DEFINITIONS`/`plan_tools` untouched** (respects the plan_tools "do not delete" rule). | ~690 → ~15 + 20 handlers | L | Moderate: load-bearing chat execution but verbatim extraction; `propose_order` still routes through the guarded chokepoint. Covered by chat/tool tests. |

### DELETE (dead — grep-proven, no dynamic dispatch)

| Impact | file:line | Problem | Concrete fix | loc_delta | Effort | Risk |
|---|---|---|---|---|---|---|
| Quick win | `ai/chat/agent.py:181` & `:145` | `_build_final_messages` (181) has **zero non-test callers**; `_synthesis_system_prompt` (145) is called only by it. Orphaned by the 2026-07-08 `loop.py` migration (mobile now uses `_system_prompt` + native tool parsing). | Delete both functions + their two tests (`test_mobile_chat_stream.py:406-419`). `_system_prompt`/`finalize_chat_turn`/`ChatRunResult` stay (live shared core). Grep confirms only the mutual pair + tests reference them (worktree hits are stale side-branches, not main). | -65 | S | Low — clean orphaned island, not safety-adjacent. |

### CONSOLIDATE (dedup to one source of truth)

| Impact | file:line | Problem | Concrete fix | loc_delta | Effort | Risk |
|---|---|---|---|---|---|---|
| High | `frontend/components/workspace/widgets/orders-widget.tsx:343` | ~20 widgets each hand-roll `[data, loading, error]` + `useEffect`-fetch + (7 of them) `setInterval` poll; races/unmount handled inconsistently (23 widgets use `useState`, 7 poll). | Add `lib/use-widget-data.ts` → `useWidgetData<T>(fetcher, {deps, pollMs})` returning `{data, loading, error, refresh}` with abort-on-unmount + interval cleanup built in (generic sibling of existing `use-watchlist-market.ts`). Migrate incrementally; pair with `WidgetError`/skeleton so empty/error branches share one render path. | -150 | L | Low-moderate, broad touch — migrate a few widgets/commit; watch multi-tab widgets (orders = filled+rejected, needs 2 data slots). |
| Med | `frontend/lib/api.ts:175` | `formatMoney` lives here **and** is redefined verbatim in `order-proposal-card.tsx:45`; ~15 more files (7 widgets + `company-info-sections.tsx`, `watchlist-panel.tsx:59`, `journal-widget.tsx:43`, etc.) re-roll the same `toLocaleString(...currency/pct)` incantation → display rules drift. | Create `lib/format.ts` exporting `formatMoney`, `formatMoneyCompact`, `formatMillions`, `formatPct(signed)`, `formatNum`, `formatQty`; replace each local copy with an import. Keep signed variants (journal's ±$) as one signed helper. | -100 | M | Low — pure display layer, no money math (backend stays Decimal/TEXT). In-browser verify. |
| Med | `ai/perception/context.py:114` | `_compute_rvol_field` (114) is a near-verbatim copy of `api/main.py:2147` (its own comment says so) but **already drifted** (main takes `session=`, returns unrounded; context has no session param, rounds to 2, no try/except); `_market_session` (69) re-implements what main cleanly imports from `brokers.market_clock.market_session` — same logic in 3 shapes. | Delete `context.py`'s local `_market_session`, import `market_session` from `brokers.market_clock`. Extract shared RVOL logic into `signals/volume_profile.py` (`rvol_field(symbol, bars, session)`) called by both. Co-locate the technicals block (86-166) in `signals/` for future reuse. | -80 | M | Low-moderate — read-only, fail-soft, has tests. **Reconcile the rounding/session-param difference** (main unrounded, context rounds) so callers keep precision. |
| Med | `api/main.py:1838` + `lib/api.ts:516` | **Two parallel watchlist APIs.** Legacy singular `/api/watchlist` (GET 1838, PUT 2438, POST 2446, DELETE 2458) + client twins `fetchWatchlist`/`saveWatchlist`/`addWatchlistSymbol`/`removeWatchlistSymbol` coexist with the newer `/api/watchlists` multi-list family. `saveWatchlist` has **zero callers**; the other three are used only by the dying legacy `/trading` page (`trading-page.tsx:73`, `watchlist-panel.tsx:143/159`). | **Delete `saveWatchlist` now** (provably dead). Repoint the two legacy-UI callers to `/api/watchlists` (active list), **then** delete the 3 remaining legacy client fns + the singular `/api/watchlist` routes. Do not delete the coupled three before the `/trading` repoint. | -120 | S (saveWatchlist) / M (rest, gated) | Deletion of the three is gated on legacy-UI removal (callers confirmed live). Not safety-adjacent. |
| Med | `brokers/alpaca.py:600` | `get_stock_bars_range` (600) / `aget_stock_bars_range` (624) build a **byte-identical** params dict (timeframe/start/end/limit/feed/sort), differing only in sync vs `await`; same twin duplication for `get_stock_bars`/`a…`, `get_account_activities`/`a…` (452/462), `get_portfolio_history`/`a…` (426/438). | Extract param assembly into pure helpers (`_bars_range_params(...)`, `_activities_params(...)`); each sync/async pair becomes param-build + `get`/`aget` page call. **Keep both entrypoints** (both are real callers). | -60 | S-M | Moderate: `brokers/` but the **data-fetch** side, NOT the execution chokepoint. Keep sync+async signatures identical; broker tests cover it. |
| Med | `config/settings.py:561` | `save_watchlist`(561), `add_watchlist_symbol`(643), `remove_watchlist_symbol`(666), `update_watchlist`(604) each hand-roll the same iterate-`lists`→rewrite-matching-id→`_save_watchlists_state`→re-resolve pattern (~90 lines). | Add `_mutate_list(list_id, fn)` (resolves target, default active; maps `fn` over the match; raises on unknown id; saves; returns updated symbols). Rewrite the four mutators as 2-4 line calls. `create_watchlist`/`delete_watchlist` stay (structural, not per-list edits). | -60 | S | Low — watchlist state only, no money/execution path. Settings tests cover it. |

### OPTIMIZE (hot-path, behavior-preserving)

| Impact | file:line | Problem | Concrete fix | loc_delta | Effort | Risk |
|---|---|---|---|---|---|---|
| High | `db/analytics.py:782` | One `ai_performance_context()` call (chat `get_performance_insights`, `/api/performance/ai-context`, retrospective) triggers **~7-8 full `list_trade_events(limit=100_000)` scans** (635, 644, 605, 809, 816 + `trade_summary_stats`/`daily_trade_stats`/`symbol_trade_stats` in `db/trades.py:472/512/548`) and runs `compute_round_trips`+`realized_summary` trio **twice** on identical data (660/675-677 inside `performance_dashboard`, then again at 801/854-861). | Load `history_events` **once** at the top (single `list_trade_events` through `to_date`); derive `period_events` by in-memory date filter; compute round-trips + the 3 realized summaries once; thread into a `performance_dashboard` that accepts optional pre-loaded events/round_trips (default `None` = self-load, preserves other callers). Target ~7-8 scans + 2 computes → 1 + 1. | -60 | M | Read-only analytics. **Assert identical output**; real/paper scoping (`real_only`/`paper_only`) must flow through the single load. |
| Med | `db/sqlite.py:502` | `get_all_latest_snapshots` (hit on **every** dashboard `/api/snapshot` poll) does a `GROUP BY` over `poll_runs` then **re-queries `poll_runs` per row** for the latest id (522-532), then `_snapshot_from_run` fires 4 SELECTs (250-263) + `_load_analytics` 1 more → ~6×N queries for N accounts. | One window-function query: `ROW_NUMBER() OVER (PARTITION BY broker,account_id ORDER BY captured_at DESC, id DESC)=1` for latest run ids, then batch position/order/account/analytics reads via `WHERE poll_run_id IN (...)` grouped in Python. ~6N → small constant. | -15 | M | Read-only display (money-as-float display layer stays as-is per audit). Add a test asserting one-snapshot-per-account stable output. |
| Quick win | `db/ai_tables.py:23` | `init_ai_tables()` is called at the top of **every** public fn (10+ sites: every memory read/write, rule, research op) with no guard → each call re-runs the full `executescript`, **18 no-op `ALTER`s that each parse-and-raise `OperationalError`** (82-112), an `UPDATE research_artifacts` scan (113-119), + an FTS probe. `db/sqlite.py:16 init_db` has the same unguarded shape on every snapshot query. | Add a module-level `_MIGRATED: set[Path]` (or `PRAGMA user_version` gate): `if db_path in _MIGRATED: return` after a cheap connect. Runs DDL/migration once per process per db_path. Apply the same guard to `init_db` and the other `init_*`. | +8 (adds guard, removes per-call work) | S | Low — DDL already idempotent; **key the guard on `db_path`** so fresh temp-DB tests still migrate. |

### SIMPLIFY (collapse parallel spellings)

| Impact | file:line | Problem | Concrete fix | loc_delta | Effort | Risk |
|---|---|---|---|---|---|---|
| Med | `db/analytics.py:854` | `performance_dashboard` already produces `full_recon`/`period_recon`/`period_realized` (675-677) + `round_trips_in_period` (660-666), but `ai_performance_context` ignores the return and **recomputes the identical values** at 854-861 + `_round_trips_in_period` (801) from freshly reloaded events — doubling the most expensive O(n) FIFO/recon work. | Have `performance_dashboard` include the raw `round_trips_in_period`/`full_recon`/`period_recon`/`period_realized` in its return (or an internal `_dashboard_core`); `ai_performance_context` reads those. **Pairs with the OPTIMIZE single-load above** (distinct fix: surfacing computed results vs deduping queries). | -30 (overlaps the -60 above) | M | Low-med — **preserve `latest_prices` threading** (context passes it into `realized_summary` at 854; dash does not) → reuse the recon, recompute only the `latest_prices`-dependent MTM piece. |
| Med (safety-adjacent) | `config/settings.py:417` | The trading-limit schema is spelled out **three times**: `DEFAULT_SETTINGS` (95-119), a 40-line per-key if/elif in `apply_settings_patch` (417-457), and a 50-line coerce+default block in `trading_config` (807-857). Renaming one cap = editing three parallel lists. | Define one table `TRADING_FIELDS = {key: (coercer, clamp_or_None)}` (e.g. `max_pending_agent_proposals → (int, (0,50))`); drive both the patch numeric branch and `trading_config` from it. **Leave the 3 semantic gates (`kill_switch_active`, `entries_halted`, `execution_mode` enum) as explicit branches.** Changes only write/read coercion, NOT `is_kill_switch_active`/`is_entries_halted`/deny-by-default/RiskEngine. | -50 | M | **Safety-adjacent** (persistence/read coercion for risk caps, not the enforcement path). Every existing clamp/coercion must be reproduced exactly. **Run a Sonnet adversarial review of the diff before commit**, per `trading/` discipline. |

---

## 3. Sequencing

### Safe quick wins — do first (low effort, low risk, immediate payoff)
1. **DELETE `saveWatchlist`** (`lib/api.ts:523`) — zero callers, drop it today. `-…` part of the watchlist row. **S / Low.**
2. **DELETE the orphaned mobile-synthesis path** (`agent.py:145,181` + 2 tests). **-65 / S / Low.**
3. **OPTIMIZE `init_ai_tables`/`init_db` with a migration guard** (`db/ai_tables.py:23`, `db/sqlite.py:16`) — kills 18 failed-ALTER round-trips + a table scan off **every** memory/snapshot read. **S / Low.** (Also closes CODE_AUDIT theme #2.)
4. **CONSOLIDATE `config/settings.py` watchlist mutators** into `_mutate_list` (`:561`). **-60 / S / Low.**
5. **CONSOLIDATE Alpaca sync/async param-builders** (`brokers/alpaca.py:600`) — data-side only. **-60 / S-M / data-fetch, not chokepoint.**
6. **CONSOLIDATE formatters → `lib/format.ts`** (`lib/api.ts:175` + ~15 files). **-100 / M / Low, UI-only.**
7. **OPTIMIZE the `get_all_latest_snapshots` N+1** (`db/sqlite.py:502`). **-15 / M / read-only.**

*→ roughly -240 LOC + two hot-path speedups, all low-risk, before touching a single god file.*

### Bigger projects — schedule (higher effort / broad touch / needs review)
- **★ SPLIT `api/main.py`** (`:232`, `:1570`) — the founder-named headline. **L.** Do it **domain-by-domain in path-scoped commits** (one router per commit), app-boot check each time. This is the single highest-leverage item; it just isn't a one-sitting job.
- **SPLIT `lib/api.ts`** into `lib/api/` + barrel. **M.** Mechanical, typecheck-verified — good second project.
- **SPLIT `tv-light-chart.tsx`** (**L**), **`workspace-grid.tsx`** (**M**), **`execute_tool` registry** (**L**).
- **CONSOLIDATE ~20 widgets → `useWidgetData`** (`orders-widget.tsx:343`). **L, broad** — a few widgets per commit.
- **OPTIMIZE + SIMPLIFY `db/analytics.py`** single-load + reuse-computed (`:782`/`:854`) as one paired change. **M.**
- **CONSOLIDATE `ai/perception/context.py`** RVOL/session dup (`:114`). **M** — reconcile rounding.
- **SIMPLIFY `config/settings.py` trading-fields table** (`:417`). **M — Sonnet-review the diff** (safety-adjacent).
- **Finish the watchlist consolidation** (`api/main.py:1838`) once the legacy `/trading` page is retired.

---

## 4. Reconciliation with `References/CODE_AUDIT.md` (2026-06-20)

**Tier 1** (dead-code deletions) was marked **✅ DONE 2026-06-20** — not revisited here.

### Tier-2 items this run RE-VERIFIED as still open — and now has a 2-reviewer-verified concrete fix for
*(line refs drifted since the audit; current lines shown)*
| CODE_AUDIT Tier-2 item (2026-06-20) | Status now | This backlog |
|---|---|---|
| "versioned migrations (theme 2)" — repeated `try/except ALTER` per init call | **Still open** | OPTIMIZE `init_ai_tables`/`init_db` guard (quick win #3). Concrete fix supplied. |
| `db/sqlite.py:448` N+1 in `get_all_latest_snapshots` "use a window function" | **Still open** (now `:502`) | OPTIMIZE row — exact window-function fix. |
| `db/analytics.py:451` "`performance_dashboard` loads the ledger twice" | **Still open — and worse than logged** (now `:622`/`782`; ~7-8 scans + double FIFO compute, not just twice) | OPTIMIZE + SIMPLIFY rows extend it. |
| Duplication: `formatMoney` (`lib/api.ts` vs `order-proposal-card.tsx`) | **Still open — broader** (~15 files, not 2) | CONSOLIDATE `lib/format.ts` row. |
| Duplication: two parallel watchlist APIs (`lib/api.ts` legacy vs `/api/watchlists`) | **Still open** | CONSOLIDATE watchlist row (with caller enumeration + safe sequencing; `saveWatchlist` deletable now). |
| Duplication: Alpaca sync/async param-builders (`get_*` vs `aget_*`) | **Still open** | CONSOLIDATE `brokers/alpaca.py:600` row — exact helper extraction. |

### Tier-2 item likely OVERTAKEN by the 2026-07-08 `loop.py` migration — verify before actioning
- **`ai/chat/agent.py:167` "sync `llm.complete` in async generator"** — the mobile brain moved to `loop.py`; the DELETE finding removes the now-orphaned `agent.py` synthesis path (`_build_final_messages`/`_synthesis_system_prompt`). The sync-LLM stall at :167 lived in that migrated-away region. **Confirm the blocking call is gone with the deletion; if a remnant survives, it's a separate small fix.** (`api/trade_routes.py:262` sync-LLM-in-handler is **still open** — untouched here.)

### Tier-2 items NOT covered by this maintainability run (still open — mostly correctness; owned by the separate bug hunt)
`api/main.py:353` settings-PUT skips `clear_settings_cache()` · `imports/alpaca_activities.py:34-37` `parse_dollar` for qty · `ai/chat/memory_extract.py:139` redundant dedup scan · `brokers/market_stream.py` `_ensure_baselines` race / stale `day_high`/`day_low` (may be partly touched by recent quote-freeze commits — unverified) · `brokers/alpaca.py:863-912` `get_option_quote` silent swallow · `ai/retrospective.py:529` hardcoded `get_broker("alpaca")` · condition-summarizer dup (`tools.py:583` ≈ `retrospective.py:604`) · `imports/schwab_transactions.py:58` journal→other · `ai/chat/alert_intent.py:13` regex typo · `trading/sync.py:149` private-attr reach. **These are correctness/smell items, not maintainability fat — deferred to the bug run, not this backlog.**

### Net-new beyond the 2026-06-20 audit (this run's additions)
The **api/main.py god-module split** is the biggest net-new item — it was **not** in the 2026-06-20 audit (which flagged only a single `main.py:353` cache bug), because the file has grown substantially since. Also net-new: the `lib/api.ts` / `tv-light-chart.tsx` / `workspace-grid.tsx` / `execute_tool` splits, the `ai/perception/context.py` copy-of-main-helpers (perception layer landed post-audit), the ~20-widget fetch-scaffolding hook, the `config/settings.py` watchlist-mutator + trading-schema triple-listing, and the `ai/chat/agent.py` orphaned-synthesis DELETE (created by the 2026-07-08 `loop.py` migration).

### "Deliberately NOT now" (CODE_AUDIT) — respected, nothing here touches them
`plan_tools` (`ai/chat/tools.py`) left intact (the execute_tool split explicitly keeps it) · Schwab `NotImplementedError` order stubs untouched · money-as-float snapshot/base display layers untouched (the N+1 fix changes query *shape*, not the float display) · parked widget work (watchlist-v2, scans/memory, command-console) excluded from the widget-hook migration.