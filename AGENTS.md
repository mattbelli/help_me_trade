# Help Me Trade — Agent Brief (codebase map)

*Rewritten 2026-07-17, every claim verified against the code that day. `CLAUDE.md` (auto-loaded) owns the
mission, working agreements, and safety rules — this file is the MAP. Current focus: `References/HANDOFF.md`.
Which References/ doc is current: `References/INDEX.md`. Plan/roadmap: `References/BUILD.md` (~50KB);
dated journal: `References/BUILD_LOG.md` (**grep it, never read it whole — 350KB**).*

**What this is:** an autonomous trading agent — one loop: PERCEIVE → REASON → DECIDE → ACT → DELIVER →
LEARN. FastAPI backend (port 8000) + Next.js workspace cockpit (port 3001) + Expo phone app; SQLite (WAL).
Runs on an always-on Linux box ("serv") as systemd units: `hmt-backend`, `hmt-frontend`, plus standalone
market-data collectors `hmt-flow-collector` (OPRA options socket, `tools/flow_collector.py`) and
`hmt-equity-collector` (equity tape wildcard socket, `tools/equity_collector.py`) — env-interlocked so the
API and collector never both own a socket. Brokers: Alpaca (paper execution + SIP data), Schwab (READ-ONLY
real accounts + futures data). LLM: provider-agnostic factory. Autonomy ladder: alerts ✅ → phone-approval
proposals ✅ (the headline unlock) → bounded auto-execute (in progress, hard-gated).

## Backend map

| Subsystem | Key files | Purpose |
|---|---|---|
| **★ Execution chokepoint** | `trading/execution_service.py` (`submit()` — the ONLY broker path, kill-switch first), `trading/risk_engine.py` (`validate()` gates every order: caps, buying power, concentration, daily-loss breaker `db/risk_breaker.py`), `trading/models.py` (frozen `OrderIntent`/`RiskCheckResult`), `trading/single_writer.py` (kernel `flock` on `<ledger>.writer.lock` — a second backend on the same DB refuses to boot, so the per-process submit lock stays a real invariant) | Deny-by-default; paper before live; LIVE hard-blocked. Adversarial review required on any change here |
| **Proposals / approvals** | `trading/proposals.py` (approve/reject, phone proposals, expiry sweep, push retry), `trading/proposal_intake.py` (`propose_trade`, `auto_execute_entry`, scan-fire intake + idea-shadow registration), `db/chat_approvals.py`, `db/push_deliveries.py` | The propose → phone-approve → execute flow |
| **Auto engines** | `trading/exit_engine.py` (stop/target/trail over `db/managed_positions.py`), `signals/strategy_scan.py` + `strategy_kinds.py` + `strategy_universe.py` (registry-driven entry scans → propose), `signals/exit_evaluators.py` | Every strategy ships DISABLED + propose-mode; founder arms manually |
| **★ Idea ledger** | `db/idea_outcomes.py` (`register_idea`, dedup, fabricated-probability wall), `research/idea_tracker.py` (`sweep_open_ideas` — Grading Law resolution: target/stop/time, SPY alpha leg, stop-wins tie-break), `research/book_mechanisms.py` (closed mechanism enum + n-gated pack scoreboard for `book:*`; tracker unchanged; never a fabricated 0.7), `db/lane_stats.py` (bump/gauge meters), `signals/pipeline_audit.py` (silence alarms + daily canary), `config/idea_engine.py` (constants = "law") | EVERY idea outcome-tracked, taken or not; calibration ground truth |
| **Decision scoring** | `signals/decision_scorer.py` + `research/artifacts/` (offline-trained calibrator/EV tables), `signals/strategy_stats.py`, `signals/confluence_score.py` (unusualness, NOT a win%), `ai/journal_review.py` (predicted-vs-realized calibration) | Win% displayed = calibrated, never fabricated |
| **Options flow** | `signals/options_flow/` — `runner.py` (pipeline host), `dbn_stream.py` (Databento OPRA ws — the ONLY print transport since 08-09; the capped Alpaca `stream.py` was retired), `poll.py`, `detect.py` (UV-1/2), `sweeps.py`, `convergence.py`, `opening_pressure.py` (index gauge), `gth_tape.py`/`gth_morning.py` (overnight GTH tee + morning line), `scan.py` (EOD); `db/flow_rarity.py` stamps per-name empirical `rarity_p` on sweep/block (corpus lookup, never on the poll path); `db/flow_conviction.py` ranks the widget list as rarity×tenor×OTM×type (perception, not a forecast) | Runs in `hmt-flow-collector`, NOT the API (`OPTIONS_FLOW_EXTERNAL_COLLECTOR=1`) |
| **Equity tape** | `signals/equity_tape.py` (blocks/dark pool), `signals/equity_stream/` (wildcard ws + detectors + `recorder.py` raw-frame tap), `signals/abnormality/` (5m range×volume vs own-norm + peer cell; rides the collector, not the API); record→replay→A/B harness `tools/equity_tape_replay.py` / `equity_collector_replay.py` / `equity_tape_ab.py`; Rust port `collectors/equity_rs/` (spec `References/EQUITY_COLLECTOR_RS_SPEC.md`) | Runs in `hmt-equity-collector` |
| **Other perception** | `signals/`: `regime.py` (FRED/VIX), `macro_substrate.py` (credit/curve/vol axes + macro calendar), `gamma_exposure.py` (SPX dealer gamma/vanna/charm off `black76.py` + `db/oi_walls.py`, stored by `db/gamma_exposure.py`, cron `tools/gex_refresh.py`), `momentum.py`, `volume_profile.py` (RVOL), `abnormality/` (magnitude tell — Parkinson RV × window volume, both-side nightly base_state, own `abnormality_outcomes`), `earnings_edgar.py` + `earnings_refresh.py`, `finra.py` (short interest), `filings/` (Form 4/13D + congress), `social_buzz.py` (post-level Reddit ingest + hourly rollup + `query("social")`), `session_node_strategy.py`, `screener.py`; **`query("flow_history")`** = whole-market OPRA rollup corpus (`db/flow_corpus.py`, 16GB, every listed name — perception rank, not a forecast) | Perception sources — "no proven edge" is never a reason not to build one |
| **Rules/alerts** | `signals/rule_schema.py` + `scan.py`, `ai/rules/engine.py`, `signals/backtest.py` | User rules: ~40-metric registry, transitions + cooldown |
| **Delivery** | `signals/notify.py` (persist-then-deliver chokepoint, `INBOX_ONLY_KINDS` demotion), `signals/push.py` (phone, budget-gated), `db/notifications.py`, `db/device_tokens.py`, `db/live_activities.py` | One notify path; push budget in settings |
| **AI — chat** | `ai/chat/loop.py` (ONE provider-agnostic tool loop for all surfaces), `web_agent.py` (AI-SDK stream for Next proxy), `mobile_agent.py` (SSE), `tools.py` + `tool_specs.py`, `context.py`, `memory_extract.py` | **`trading` toolset NEVER exposed to chat** — propose-only |
| **AI — other** | `ai/llm/` (factory: anthropic/openai/deepseek/openrouter/lmstudio/ollama/mock), `ai/research/` (engine v2 + `setup_scan.py` = nightly idea scan), `ai/memories/` (judge-gated writes + FTS5 retrieval), `ai/reflection/`, `ai/insights.py`, `ai/retrospective.py`, `ai/perception/` (read-layer: context + resource registry) | |
| **API host** | `api/main.py` (~960 lines: app + lifespan + 22 app-level routes) mounts the routers `ai/trade/performance/strategy/notification/scan/schwab/journal/ticker/social/orders/device/fill_orphan/fusion/workspace/reports/alert_drawing/status_page/bars/scanner/flow/watchlist`; `api/hub.py` = the live `market_hub`; background work lives in `api/*_jobs.py` (`poll/scan/earnings/trade/tape/flow/schwab/ops/fusion/research`) and `api/background_jobs.py` is the registry (what runs, cadence, flags) over `api/jobs.py`; shared `alpaca_guard._require_alpaca`; `api/loop_probe.py` = the stall watchdog + `GET /api/debug/threads` (per-thread CPU seconds + stacks — the profiler on this box, where py-spy/ptrace is denied). **Ratchet:** `tests/test_main_ratchet.py` fails if main.py gains inline routes/lines — new endpoints go in a `*_routes.py`, new jobs in a `*_jobs.py` + the registry | |
| **DB — ledger** | `db/trades.py` (event ledger), `db/realized_pnl.py` (**net-cash = truth**), `db/fifo_round_trips.py` (approximate — never authoritative), `db/decision_journal.py`, `db/order_executions.py` (audited, adversarial-review zone), `db/account_scope.py` (selector→ledger id resolver, label-fragile), `db/retention.py` | Money = TEXT/Decimal |
| **Brokers** | `brokers/alpaca.py` (primary), `brokers/schwab*.py` (read-only real + futures; `schwab_market_stream.py` Account Activity → debounced account-scoped ledger refresh via `trading/position_trigger.py`), `factory.py`, `hybrid_market_data.py`/`hybrid_market_stream.py` (per-asset-class routing: futures→Schwab, equities→Alpaca), `market_stream.py` (browser-facing WS hub), `bar_builder.py` (server-side minute-bar authority for the chart: prints + Alpaca bars/updatedBars → `bar` frames + `/api/bars` tail splice); `/api/bars` (`api/bars_routes.py`) is newest-window-first + `before=` pages via `MarketDataProvider.aget_stock_bars_latest` — no prewarm, no `days`, `options_chain.py` | Schwab NEVER a hard dependency; never burst-request it (WAF) |
| **Imports** | `imports/schwab_transactions.py`, `alpaca_activities.py`, `money.py` (Decimal parsing), `option_symbol.py` | Dedup by `external_key` |

## Frontend map (`frontend/`)

| Subsystem | Key files | Purpose |
|---|---|---|
| **Workspace shell** | `app/page.tsx` (workspace IS the main URL), `components/workspace/workspace-grid.tsx`, `lib/workspace/widget-registry.tsx` (**27 widgets**: chart, watchlist, options-flow, index-pressure, idea-outcomes, agent-proposals, notifications…), `lib/workspace/channels.ts` (6 colored symbol-link channels), `use-workspace-store.ts` (workspaces = named page groups) | Tickers everywhere click-to-focus-chart via channels |
| **Terminal** | `components/workspace/terminal/terminal.tsx` (bottom drawer: chat + commands), `lib/workspace/use-workspace-commands.ts` | |
| **★ Chart engine (ours)** | `lib/chart-engine/` — `engine.ts`, `canvas2d.ts`, `painter.ts` (RN-Skia later), pure `scales/ticks/series/merge` + vitest; `components/trading/native-chart.tsx` | TradingView is DELETED — this is the only chart. Controls in top toolbar, never floating |
| **Chat** | `app/api/chat/route.ts` (proxies to Python brain via `lib/python-server.ts`; `CHAT_BRAIN=ts` = legacy revert), `components/assistant-ui/` | Brain is Python; Node never places orders |
| **Watchlist** | `lib/workspace/watchlist-columns.tsx` (19-col registry, density presets), `widgets/watchlist-widget.tsx` | Per-list settings sync server-side |
| **Standalone pages** | `/performance`, `/research`, `/board` (kanban ← `References/board.json`), `/memories`, `/agents`, `/rules`, `/automations`; `/trading` `/charts` `/trades` = dead redirects | |
| **Mobile web** | `components/workspace/mobile-workspace.tsx` — 4-tab bottom-nav surface, NOT the grid | Phone ≠ desktop grid; widgets never overlap |
| **API clients** | `lib/api.ts` (main, 1.7k lines) + `journal/performance/reports/scans/strategies/ticker-info/trades-api.ts` | |

**Desktop shell (`desktop/`)**: Tauri 2 thin window on the serv cockpit (remote URL, no IPC) + global kill-switch
hotkey / always-on-top / tray — Rust only, built per-OS on the PC/Mac (`desktop/README.md`).

**Phone app (`mobile/`)**: Expo/RN — `src/features/approvals/` (proposal card), `src/push/` (lock-screen
approve w/ biometric, Live Activities, APNs), `src/api/chat-stream.ts` (SSE from `mobile_agent.py`).

## Conventions an agent must know (details in CLAUDE.md — these are the map-level ones)

- **Money is `Decimal`** in `trading/` (float→Decimal seam at propose_order); SQLite stores money as TEXT.
- **Net-cash realized P&L is truth; FIFO is approximate** — never present FIFO as authoritative.
- **Tests**: ~4,000 collected. Run TARGETED modules (`python -m pytest tests/test_x.py -q`) — the full
  suite hangs on network/ws tests. Require pytest's own exit code AND the "N passed" line (`| tail` masks
  hangs). Options-flow convergence tests need the `dedup_store` fixture.
- **Lint/typecheck**: `ruff check .` (config `ruff.toml`, py312, line 100) + `cd frontend && npm run typecheck`.
- **Git hook** (`tools/hooks/commit-msg`): product-code commits must also stage `References/board.json` or
  `BUILD.md`; escape hatch `[no-board]`. Docs/tests-only commits are exempt.
- **Shared working tree**: stage by explicit path, never `git add -u/-A`; one tree-writer at a time.
- **Secrets** in gitignored `.env`; `data/` is gitignored (real ledger + user financial exports).

## Run / deploy

```bash
# serv (production — the normal case): build frontend first if it changed, then
tools/serv-deploy.sh [backend|frontend|both]   # kills MainPID; systemd relaunches. No sudo.
# units are SYSTEM units: systemctl status hmt-backend (not --user)

# local dev
source .venv/bin/activate && uvicorn api.main:app --reload --port 8000
cd frontend && npm run dev   # 3001
```

⚠️ Never `npm run build` under a live `next start` outside a deploy (stale-chunk breakage). Kill any dev
server you spawn (3005+). Journald timestamps are UTC (ET−4). Full ops detail: `References/SERVER_SETUP.md`.

## Maintenance

This file is the fast brief agents trust — a stale map is worse than no map. When a subsystem ships,
moves, or dies, refresh the affected rows **in the same round** (the HANDOFF/BUILD update step). Rewritten
2026-07-17 after drifting badly for ~3 weeks; don't let it drift again.
