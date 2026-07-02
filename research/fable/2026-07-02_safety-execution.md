# Adversarial Safety Audit — Execution & Risk Core
*Fable 5 audit · read-only red-team · 2026-07-02 · the intent→broker chokepoint.*

Live-money exposure is currently gated (default `execution_mode=dry_run`, `live` hard-blocked), so most findings are forward-looking to the **paper→bounded-auto-execute** rung — but two are exploitable *today* if config drifts, and the auth hole is live now.

## 1. Attack-surface map
Every execution funnels through `ExecutionService.submit()` (`trading/execution_service.py:24`). Callers: phone approve (`proposals.py:209`), auto-entry (`proposal_intake.py:227`), auto-exit (`exit_engine.py:361`), momentum rebalance (`momentum.py:485,539`), chat `propose_order` (`ai/chat/tools.py:760`). **No path writes a broker order except through `submit` — the chokepoint discipline is genuinely airtight; no side door found.**

Per-submit sequence: kill-switch (34) → write `pending` audit row (54) → `RiskEngine.validate` (72) → branch: `dry_run` logs (94), `live` **hard-rejects** (105), `paper` places via Alpaca (113), unknown rejects (162). `validate` runs 10 gates (kill-switch, account-execution-capable, symbol tradable, qty bounds, snapshot freshness ≤5min, sell/short, live quote, buying power, per-order value, concentration, open-order + daily counts).

**Genuinely airtight:** Schwab read-only (4 layers: `execution_capable=0`; RiskEngine rejects non-capable; symbol-tradable gate requires `isinstance(broker, AlpacaAdapter)`; `SchwabAdapter.aplace_order` raises `NotImplementedError`). LIVE gate (`mode=="live"` unconditional reject). Phone double-approve (`claim_proposal_for_approval` atomic `UPDATE ... WHERE status='pending_phone' AND expires_at>now`; broker idempotency via `client_order_id=execution_id` + 409-recovery).

## 2. Ranked findings

### CRITICAL
**C1 — `execution_mode=="paper"` does NOT guarantee a paper broker; it trusts `api_base_url`.** `execution_service.py:113–161` places against whatever `AlpacaAdapter.from_settings()` resolves; endpoint comes from `api_base_url` (`settings.py:677`, `alpaca.py:115`). `is_paper_trading()` exists (`settings.py:862`) but is **never called in the execution path**. Scenario: `api_base_url` → live + live keys (env, or via the unauthenticated `PUT /api/settings` — C4/H2) while `execution_mode` stays `"paper"` → the "paper" branch fires a **real live order, silently, UI still labels it paper.** Fix: in the paper branch assert `is_paper_trading()` (or `"paper-api" in adapter._base_url`) before `aplace_order`; else reject. Cheapest high-value guard here.

### HIGH
**C2 (High/Critical) — Concurrency TOCTOU: buying-power / per-order-value / concentration / open-orders caps don't see in-flight orders.** All read a snapshot up to 5 min old (`risk_engine.py:214`) and never add this system's own `pending`/`submitted` executions to exposure (`existing_mv` from snapshot only, `:397`). The scan/exit/rebalance/approve loops call `submit` concurrently in one event loop (`api/main.py:1226–1240`), interleaving at every `await`. Two $40k buys, `max_order_value_usd=50000` → each passes the *per-order* cap, both see the same stale snapshot → combined $80k, ~2× concentration. **No aggregate/global notional cap exists anywhere.** Fix: fold live in-flight notional into buying-power/concentration; add a hard global daily notional cap.
> *Correcting prior finding (b):* the daily-*count* double-pass is largely already closed — the `pending` row is written before `validate` and `count_executions_today` counts `pending`/`submitted`. The unclosed race is the **snapshot-based** caps, not the counter.

**H1 — No daily-loss / drawdown circuit breaker (VERIFIED).** `risk_engine.py` has zero P&L-aware gate; every check bounds a single order. Nothing halts after the account bleeds $X across many valid trades. The defining missing guardrail for bounded-auto. Designed in §3.

**H2 — Auth is optional and unscoped (VERIFIED).** `api/auth.py:17` — `auth_enabled()` returns False when `API_TOKEN` unset → every endpoint open to any host peer (tailnet/LAN). Even set, it's **one static token, no scope split**: the same token that reads the watchlist can `PUT /api/settings` (`main.py:1452`) to **disarm the kill switch, raise `max_order_value_usd`/`max_position_pct`, enable shorting, set `execution_mode`** (merge accepts all, `settings.py:373–401`). Confused-deputy + full risk-control bypass. Fix in §5.

**H3 — Rebalance is exempt from count throttles, pace-limited only by `sleep`.** `risk_engine.py:423` exempts open-orders + daily-order caps when `intent.rebalance=True`. A 90+-name rebalance (`momentum.py:461–554`) fires unbounded count, gated per-leg only by real caps + `_REBALANCE_PACE_SECONDS`. With C2 (no aggregate notional) + fractional enabled for rebalance, a mis-sized `notional_usd` deploys the whole book with no count ceiling. Paper-fenced today; a live unlock needs a per-run notional ceiling + account-dedicated guard (the code comment at `momentum.py:352` flags its own missing guard).

### MEDIUM
**M1 — Fill-attribution failure silently orphans a position from exit management.** `record_strategy_fill` swallows exceptions → `None` (`position_ledger.py:153`); `reconcile_open_executions` fail-soft (`fill_tracker.py:157`). BUY fills but `managed_positions` row fails → exit engine never sees it → an auto-strategy long with **no stop/target/time-exit, indefinitely**. Fix: raise a `critical` alert on fill-with-missing-ledger-row; reconcile managed rows from broker positions.

**M2 — Kill switch also blocks protective exits.** `submit` rejects ALL orders when active (`execution_service.py:34`), including auto-exits. The emergency stop freezes your ability to *close* risk. Design decision: exits should arguably be allowed (or a separate "halt-new-entries" state).

**M3 — Concentration/sizing trusts snapshot floats with no bad-tick guard.** `_size_candidate` (`proposal_intake.py:81`) + concentration use a single quote with no sanity band; `_live_market_price` only rejects `<=0` (`risk_engine.py:531`). A bad tick mis-sizes qty. Add an anomaly guard (reject quotes deviating >X% from prior close).

### LOW
**L1** daily-count cap self-counts its own pending row (conservative off-by-one). **L2** `approve_proposal` makes `expected_hash` optional (`proposals.py:149`); HTTP route requires it, so fine today. **L3** orphan-reconcile can mark a `pending` paper row `rejected` after the broker accepted it if `aget_order_by_client_id` errors >120s (`fill_tracker.py:66,86`) — audit-trail drift, not double-execution.

## 3. Daily-loss / drawdown breaker — design
**Measure:** per-account intraday realized + unrealized P&L (Decimal), from the snapshot RiskEngine already loads + today's realized closes. Budget `max_daily_loss_usd` (optionally per-strategy via existing `managed_positions.strategy_id`). Baseline = first snapshot of the ET session (codebase already snapshots daily equity, commit `d02ac3e`).
**Hook:** new gate in `RiskEngine.validate`, after snapshot load / before buying-power (`risk_engine.py:~253`), **only for exposure-increasing orders** (`buy`/`is_short_open`) — never block closes/exits. `if (baseline_equity - current_equity) >= max_daily_loss_usd: block`.
**Two-stage:** (1) soft — block new entries on budget breach (exits stay open). (2) hard — on larger breach (1.5× or equity < floor), auto-arm the kill switch + critical push. Every entry re-checks in `submit`, so the soft gate immediately stops scan/rebalance/auto-entry.
**Reset:** re-baseline at ET session open (scheduled). Never reset intraday.
**vs kill switch:** the breaker is the *automatic* trigger; the kill switch stays the manual master off; keep them distinct so a manual disarm doesn't silently clear a loss-triggered halt.

## 4. Guardrails required BEFORE any human-tap-free execution (ranked)
1. **Mandatory auth + scope split** (H2) — blocking. 2. **Paper-endpoint assertion** (C1) — blocking. 3. **Daily-loss breaker** (§3) — blocking (the rung is *defined* by it). 4. **Hard global daily notional cap** (covers C2 + H3) — blocking. 5. **Concurrency-safe caps** (fold in-flight into buying-power/concentration, C2) — blocking. 6. **Per-strategy exposure caps** (strategy_id exists, caps are account-wide) — high. 7. **Bad-tick guard on the sizing quote** (M3) — high. 8. **Audit-trail invariant** (placed order → always terminal local status; fill → always a managed row or critical alert) — high. 9. **Orphaned-position monitor** (M1) — medium. Each ships with a test.

## 5. Auth/authorization fix — concrete
1. **Mandatory for mutating routes** — `PUT/POST/DELETE` (esp. risk-config or order placement/approval) require a token **even if `API_TOKEN` is unset** (fail-closed: no token configured → refuse mutations). Today it's reversed (`auth.py:38,60` allow-all when unset).
2. **Split read scope from risk-mutate scope** — `API_TOKEN` (read + place-within-caps) vs `API_ADMIN_TOKEN` gating a denylist: `PUT /api/settings` for `trading.*` (`kill_switch_active`, `execution_mode`, `max_order_value_usd`, `max_position_pct`, `max_daily_orders`, `allow_short_selling`) + any kill-switch endpoint. Enforce in `SecurityMiddleware.dispatch` (`middleware.py:21`) by method+path (+ patched keys). Constant-time compare already used.
3. **Separate the kill-switch from generic settings** — dedicated `POST /api/kill-switch` requiring admin token, so the broad settings PUT can't loosen it as a side effect. **Asymmetric:** arming (safety ON) needs only the normal token; disarming (OFF) needs admin.

**Bottom line:** the chokepoint architecture + Schwab-read-only fencing are genuinely strong. The real-money killers before the next rung are **C1 (paper mode not tied to a paper endpoint)** and **H2 (optional, unscoped auth over the risk-config surface)** — both reachable via the same unauthenticated `PUT /api/settings` — plus the **absent daily-loss breaker (H1)** and **snapshot-TOCTOU / no-aggregate-cap (C2)**. Fix those four before any order executes without a human tap.
