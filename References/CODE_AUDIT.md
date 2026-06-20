# Code audit — findings + cleanup backlog

*Read-only audit by 6 subagents (2026-06-20), triaged with judgment. Nothing here is fixed yet — it's a
backlog to burn down **opportunistically** (clean a file when we open it for a feature), not in one risky
sweep. file:line refs were accurate at audit time; re-verify before editing.*

## Headline themes
1. **Money-as-float in the *display/snapshot* layers** — `db/sqlite.py` snapshots, `brokers/base.py`
   dataclasses, `db/paper_orders.py`, `db/chart_features.py` price alerts all use `REAL`/`float`. **Important
   nuance: the authoritative paths are clean** — the trade ledger (`trade_events`), order-execution audit, and
   the whole `trading/` risk path are TEXT/Decimal. So this is broker-reported display data, not ledger math.
   Worth tightening eventually; not a correctness bug in execution today.
2. **Migrations via repeated `try/except ALTER TABLE` on every init call** (`ai_tables`, `trades`, `sqlite`)
   — runs ~5 no-op queries per DB call. A versioned migration table would fix it.
3. **Sync LLM / blocking calls inside async handlers** (`agent.py:167`, `trade_routes.py:262`) — stalls the
   event loop during generation. Real, fixable.
4. **Sparse *why*-comments outside the safety core.** The chokepoint (`risk_engine`, `execution_service`) is
   well-commented; much of the Composer-generated supporting code explains *what* (the code already shows
   that) but not *why*. The Tier-3 list below is the targeted backlog — we add these as we touch files, never
   blanket-comment.

---

## Tier 1 — Safe quick wins (dead code, low-risk deletions) — ✅ DONE (2026-06-20)
*Removed; 312 tests still pass, whole-repo ruff clean. `brokers/alpaca.py::from_env` was KEPT (real caller).*
Verified zero callers by the auditors; cheap to remove, shrinks the surface. Do in one small PR with tests.
- `api/deps.py` — **entire file dead** (`require_api_auth` unused; auth is middleware). Delete.
- `ai/chat/stream.py` — `reasoning_chunks` (no callers).
- `ai/chat/brain.py` — `EPHEMERAL_MODES` / `PERSISTED_CHAT_MODES` constants (unused).
- `brokers/alpaca.py` + `brokers/schwab.py` — `from_env` aliases (pass-throughs, no non-test callers).
- `db/chart_features.py` — `list_price_alerts_for_symbol` (one-line unused wrapper).
- `imports/money.py` — `decimal_to_api` (unused in prod; confirm vs `decimal_to_str`).
- `trading/sync.py:85-88` — dead no-op `qty_val` block; `:164` unused `full_backfill` param.
- Frontend: `components/placeholders/coming-soon-page.tsx`, `components/agents/chat-session-sidebar.tsx`
  (superseded by the workspace thread list) — unimported. Delete.

## Tier 2 — Worth doing (real bugs / perf / duplication)
Each needs tests + (for `trading/`/`db/`) a review. Prioritized.
- **`api/main.py:353` — settings PUT doesn't call `clear_settings_cache()`** (only clears llm/broker caches).
  Stale settings can be served until the next cache miss. *Med — verify + fix.*
- **`imports/alpaca_activities.py:34-37` — `parse_dollar` used for `qty` + `qty <= 0` rejects fractional
  shares.** Could mis-handle fractional/options quantities on sync. *Med.* Use `parse_quantity`.
- **`ai/chat/memory_extract.py:139` — redundant dedup scan** duplicates `create_memory`'s own Jaccard dedup
  (`ai_tables.py:250`). Remove the pre-check or have `create_memory` return an "inserted?" flag. *Med.*
- **`brokers/market_stream.py` — `_ensure_baselines` race** (two concurrent subscribers double-fetch bars);
  **`day_high`/`day_low` never updated by live ticks** (seeded once, then stale → wrong on widgets). *Med.*
- **`brokers/alpaca.py:863-912` — `get_option_quote` swallows all exceptions silently** (network error vs
  no-quote indistinguishable). Log at DEBUG before returning None. *Med.*
- **`ai/retrospective.py:529` — hardcodes `get_broker("alpaca")`** for backtest bars regardless of configured
  broker; silently empty if Alpaca absent. *Med — fall back or surface a note.*
- **Async/perf:** `ai/chat/agent.py:167` (sync `llm.complete` in async generator — use `astream`/
  `to_thread`); `api/trade_routes.py:262` (sync LLM in handler). *Med.*
- **DB perf:** versioned migrations (theme 2); `db/sqlite.py:448` N+1 in `get_all_latest_snapshots` (use a
  window function); `db/analytics.py:451` `performance_dashboard` loads the ledger twice. *Med/Low.*
- **Duplication (extract a shared helper):** condition-summarizer (`ai/chat/tools.py:583` ≈
  `ai/retrospective.py:604`); `formatMoney` (frontend `lib/api.ts` vs `order-proposal-card.tsx`); the two
  parallel watchlist APIs in `lib/api.ts` (legacy single-list vs `/api/watchlists`); Alpaca sync/async
  param-builders (`get_*` vs `aget_*`). *Low.*
- **Smells:** `imports/schwab_transactions.py:58` "journal" classified as "other" (dead transfer branch);
  `ai/chat/alert_intent.py:13` regex typo `rise(s|n|s)?` (the `n` branch is unreachable);
  `trading/sync.py:149` reaches into `SchwabAdapter._client`/`_account_hash` (private). *Low.*

## Tier 3 — Targeted *why*-comments (the "help me read it" backlog)
Add these as we touch the files — they explain non-obvious intent. NOT blanket comments.
- `trading/risk_engine.py` — why `risk_price = max(limit, market)` for limit buys (worst-case notional);
  note on `models.py:OrderIntent.multiplier` that risk math ignores it (self-enforces ×100).
- `trading/execution_service.py` — the post-pass `pending` re-write is a checks-persistence call, not a state
  change; soften the "Increment 3" string in the user-facing live-blocked error.
- `brokers/alpaca.py` — the 422 + `position_intent`-strip retry (paper rejects `position_intent`); the
  class-level assets cache assumption.
- `brokers/market_stream.py` — the 1s `wait_for` poll (checks pending subs without blocking); the
  `pending_add -= initial` reconcile; the "403 already authenticated" reconnect case.
- `ai/chat/intent.py` — why five overlapping regexes (priority/fallback).
- `config/settings.py` — the double disk-read in `apply_settings_patch` (don't clobber saved secrets with env
  values); the kill-switch low-latency cache read.
- `db/corporate_actions.py` — `_alpaca_cache` is a process-lifetime cache (no TTL).

## Deliberately NOT now (don't "clean" these)
- **Parked widget work** (watchlist-v2 [disliked], scans/memory widgets, command-console) — uncommitted in
  the tree; resolve separately (revert vs commit-good-parts), not as part of this audit.
- **`plan_tools` (`ai/chat/tools.py`)** — looks dead (only tests call it) but it's the Python-side tool
  planner; the live path uses the Next.js AI SDK. Add a clarifying comment, don't delete.
- **Schwab `NotImplementedError` order stubs** — intentional (Schwab is data-in only for now).
- **Money-as-float refactor in snapshot/base layers** — broad, touches display paths; defer to a deliberate
  pass, not a drive-by.
