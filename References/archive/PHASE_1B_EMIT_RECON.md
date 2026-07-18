# Phase 1b — Emit-Site Recon Table

*Generated 2026-06-29. Read-only recon — no source changed.*
*Purpose: complete worklist for rerouting every existing notification emit through `notify()` (signals/notify.py).*

---

## ★ LOCKED ORCHESTRATOR DECISIONS (2026-06-29) — these govern the brief

**Governing principle:** Phase 1b routes EVERY emit through `notify()` for persistence + WS parity, but
**preserves each site's CURRENT mobile-push profile exactly** — no new APNs banners, none dropped. "Both
platforms" parity is achieved by the persisted inbox record (the mobile inbox screen reads `/api/notifications`),
NOT by a loud push on every event. Rationale: anti-alert-fatigue (see [[auto-entry-alert-batching]]) + keep a
money-path refactor behavior-preserving. Enabling a new push for any event later = a one-line `push=True` flip.

→ Concretely, the `push=` flag at each site = **True iff that site sends a mobile push TODAY** (i.e. has a
`send_alert_push`/`send_proposal_push` call now). Web-only sites (broadcast_alert only) get **`push=False`**.

| Decision | Resolution |
|---|---|
| **A — Proposal push (row 9)** | Option 1: `notify(..., push=False)` for persist+WS, then **KEEP** the existing `send_proposal_push(row)` + `start_proposal_live_activity(row)` calls unchanged. The interactive Approve/Reject category (`TRADE_PROPOSAL`) is the **mission-critical phone-approval unlock** — must NOT regress to a generic push. |
| **B — Exit double-push (row 12)** | `push=(exit_mode == "auto")`. Auto-exit pushes (as today via `exit_engine.py:360`); propose-exit's mobile push comes from `create_phone_proposal()` — `notify()` must NOT also push, or it double-pings. |
| **C — Execution submitted (row 10)** | `push=False`. It's a display-only cockpit broadcast, web-only today. A push here would double up with the auto-entry push (`proposal_intake.py:225`) and the exit-engine push. Fills are owned by their specific sites. |
| **D — Web-only rows get push=False (rows 1, 2, 10, 11)** | rule-triggered, signal-scan, execution-submitted, fill-status are web-only today → `push=False` (persist + WS only). They DO land in the mobile inbox via persistence (= "both" parity). Enabling a phone banner for rule-triggers/fills is a deliberate opt-in follow-up, not part of this refactor. |
| **E — Rule-trigger titles (rows 1, 2)** | Proceed with `f"{rule_name} — …"` default; per-rule-type title polish is a later pass, not a blocker. |
| **F — async** | All sites are in `async def` and already await their emit → `await notify(...)` throughout; no `notify_soon` needed. |
| **Safety** | At every `trading/` site, the `await notify(...)` must sit inside the SAME try/except isolation that wraps the old emit — a notify failure must NEVER block the execution/risk path. Adversarial Sonnet review of all 7 `trading/` sites before commit. |

**Per-row `push=` (preserve-current-profile):** True → rows 3,4,5,6,7,8(auto),13,14,15,16 · `push=False` → rows
1,2,9(+keep specialized push),10,11 · conditional → row 12 (`push=exit_mode=="auto"`), row 8-manual (`push=?push` param).

---

## Key facts before the table

### `notify()` signature (signals/notify.py:43)
```python
async def notify(
    *, kind, title, body="", severity="info",
    account_id=None, strategy_id=None,
    ref_type=None, ref_id=None, action_url=None,
    group_key=None, data=None,
    push=True, db_path=DEFAULT_DB_PATH
) -> dict
```
Persists → WS broadcasts `{"type": "notification", **row}` → if `push=True`, calls `send_alert_push(title, body, data=...)`.  
`notify_soon(**kwargs)` is the fire-and-forget sync wrapper; use wherever the old call was non-awaited.

### Allowed enum values (db/notifications.py + NOTIFICATIONS_FOUNDATION.md)
- **kind**: `proposal | fill | exit | execution | rule_triggered | price_alert | report_ready | rvol | earnings | schwab_health | rebalance | system`
- **severity**: `info | success | warning | critical`
- **ref_type**: `report | proposal | execution | rule | alert | position`

### What `broadcast_alert()` currently does (brokers/market_stream.py:247)
Sends `{"type": "alert", **enriched}` to all WS clients and appends to the 50-item in-memory deque.  
**After migration**, `notify()` sends `{"type": "notification", **row}` — the WS event type changes.  
→ **Phase 1b + 1c MUST deploy atomically** (web bell must consume `type:"notification"` before 1b goes live).

---

## Emit-site inventory: 21 raw call sites → 16 logical events

| # | Event | Current call site(s) `file:line` | Trigger | Current transports | Current payload fields | → Proposed `notify(...)` call | `trading/`? |
|---|---|---|---|---|---|---|---|
| 1 | **Rule triggered — portfolio poll** | `api/main.py:269` (Alpaca account loop) `api/main.py:326` (Schwab/broker loop) | `evaluate_on_snapshot()` fires one or more rules against a fresh broker snapshot; called from both `_poll_account` and `_poll_broker` on every poll cycle | WEB only (`broadcast_alert`) | `{source:"rule", rule_id, rule_name, action:{type,...}, broker}` | `await notify(kind="rule_triggered", title=f"{item['rule_name']} triggered — {item['action'].get('type','?')}", body=str(item["action"]), severity="info", ref_type="rule", ref_id=item["rule_id"], action_url="/alerts", data={"rule_id":item["rule_id"],"rule_name":item["rule_name"],"action":item["action"],"broker":broker_name})` — same pattern replaces both lines | No |
| 2 | **Signal scan alert (non-trade)** | `api/main.py:407` | `run_signal_scan()` fires a rule whose `action.type != "trade"`; trade-action rules instead call `propose_from_scan_fire()` and never reach this branch | WEB only | `{source:"signal_scan", rule_id, rule_name, action}` | `await notify(kind="rule_triggered", title=f"{item['rule_name']} — signal", body=str(item["action"]), severity="info", ref_type="rule", ref_id=item["rule_id"], action_url="/alerts", data={"rule_id":item["rule_id"],"rule_name":item["rule_name"],"action":item["action"]})` | No |
| 3 | **Price alert** | `brokers/market_stream.py:363` (web) `brokers/market_stream.py:384` (mobile) | Live Schwab quote stream crosses a user price-alert level in `_check_price_alerts()`; `triggered` list from `check_alerts()` | BOTH | web: `{source:"price_alert", symbol, alert_id, target_price, trigger_price, direction, note}` / mobile: title="Price alert", body=f"{sym} {dir} ${target} — now ${current:.2f}" + note, data={type:"price_alert", symbol, alert_id} | One `await notify(kind="price_alert", title=f"Price alert: {symbol} {direction} ${target:g}", body=f"{symbol} {direction} ${target:g} — now ${current:.2f}" + (f" ({note})" if note else ""), severity="info", ref_type="alert", ref_id=str(alert.get("id")), action_url=f"/watchlist/{symbol}", data={"symbol":symbol,"alert_id":alert.get("id"),"target_price":target,"trigger_price":current,"direction":direction,"note":note})` replaces BOTH calls | No (brokers/) |
| 4 | **RVOL unusual volume** | `api/main.py:724` | Watchlist symbol's real-time volume ≥ RVOL_ALERT_THRESHOLD × expected-for-time-of-day; deduped per (symbol, ET date) via `_should_rvol_alert` / `_mark_rvol_alerted` | MOBILE only | title="Unusual volume", body=f"{sym} {rvol:.1f}× normal volume ({price_str})"; no `data` dict in current call | `await notify(kind="rvol", title=f"Unusual volume: {sym}", body=f"{sym} {rvol_val:.1f}× normal volume ({price_str})", severity="info", action_url=f"/watchlist/{sym}", data={"symbol":sym,"rvol":round(rvol_val,2),"price":price})` | No |
| 5 | **Earnings milestone (t14 / t1 / t0)** | `api/main.py:808` | `_earnings_alert_once()` loop (~6h); watchlist symbol has earnings 2–14 days out (t14), tomorrow (t1), or today (t0); deduped via `db.earnings_alerts` was_sent/mark_sent | MOBILE only | title="Earnings", body=milestone-specific string, data={type:"earnings", symbol, earnings_date, milestone} | `await notify(kind="earnings", title=f"Earnings: {sym} — {milestone}", body=body, severity="info", action_url=f"/watchlist/{sym}", data={"symbol":sym,"earnings_date":earnings_date_str,"milestone":milestone})` Note: `key` dedup in `db.earnings_alerts` still controls one-send-per-milestone; `notify()` doesn't need to re-dedup | No |
| 6 | **Schwab auth offline** | `api/main.py:870` | `_schwab_health_loop()` (~6h); `auth_status()` returns `authenticated=False`; rate-limited to once per ET day via `_schwab_health_last_date["offline"]` | MOBILE only | title="Schwab data offline", body="Token invalid; re-auth needed. Run scripts.schwab_reauth or re-login." | `await notify(kind="schwab_health", title="Schwab data offline", body="Token invalid; re-auth needed. Run scripts.schwab_reauth or re-login.", severity="critical", group_key="schwab_health_offline", data={"auth_status":"offline"})` | No |
| 7 | **Schwab token expiring** | `api/main.py:880` | Same loop; `estimated_days_until_reauth ≤ 1.0`; rate-limited to once per ET day | MOBILE only | title="Schwab re-auth needed", body=f"Token expires within ~{days_left:.1f} day(s)..." | `await notify(kind="schwab_health", title="Schwab re-auth needed", body=f"Token expires within ~{days_left:.1f} day(s). Run scripts.schwab_reauth or re-login.", severity="warning", group_key="schwab_health_expiring", data={"auth_status":"expiring","days_left":days_left})` | No |
| 8 | **Morning levels report** | `api/main.py:933` (auto loop) `api/main.py:1949` (POST /api/reports/generate?push=true) | Auto: `_morning_levels_report_loop()` fires in the configured ET hour window, once per day; Manual: API endpoint triggered by operator | MOBILE only | title=report["title"], body=report["body"], data={type:"report", report_id, kind:"morning_levels"} | `await notify(kind="report_ready", title=report["title"], body=report["body"], severity="info", ref_type="report", ref_id=report["id"], action_url=f"/reports/{report['id']}", data={"report_id":report["id"],"kind":"morning_levels"})` For the manual endpoint: pass `push=True` when the `?push` query param is truthy, otherwise `push=False` (persist to inbox silently) | No |
| 9 | **Trade proposal** | `trading/proposals.py:327` (web broadcast) `trading/proposals.py:347` (send_proposal_push) | `create_phone_proposal()` mints and stores a pending proposal; line 352 also calls `start_proposal_live_activity()` | BOTH | web: `{type:"alert",source:"proposal",rule_name:"Order proposal",proposal_id,action:{type:"order_proposal",proposal_id,summary,expires_at}}` / mobile: `send_proposal_push` → APNs with `categoryId:"TRADE_PROPOSAL"` (interactive Approve/Reject), data={type:"trade_proposal",proposal_id,payload_hash,expires_at} | **⚠ ORCHESTRATOR DECISION — see Notes.** Recommended: `await notify(kind="proposal", title=f"Trade proposal: {side} {qty} {symbol}", body=summary, severity="info", account_id=account_id, ref_type="proposal", ref_id=row["id"], action_url=f"/approvals/{row['id']}", data={"proposal_id":row["id"],"summary":summary,"expires_at":row["expires_at"]}, push=False)` + keep existing `send_proposal_push(row)` + `start_proposal_live_activity(row)` calls | **YES** |
| 10 | **Execution submitted** | `trading/execution_service.py:191` | `ExecutionService.submit()` after submitting to broker (display-only cockpit broadcast, NOT part of risk path) | WEB only | `{type:"execution", execution_id, status, symbol, side, qty, execution_mode, broker_order_id?, strategy_id?}` — `strategy_id` only present when `proposal_id` starts with `"auto_entry|..."` | `await notify(kind="execution", title=f"{intent.side.upper()} {intent.qty} {intent.symbol} — {status}", body=f"Execution {execution_id} [{mode}]", severity="info", strategy_id=alert.get("strategy_id"), ref_type="execution", ref_id=execution_id, action_url=f"/executions/{execution_id}", data=dict(alert), push=True)` Note: `account_id` is NOT in scope here — not in `OrderIntent`; the execution row in DB has it but is not re-fetched | **YES** |
| 11 | **Fill status update** | `trading/fill_tracker.py:168` | `reconcile_pending_executions()` detects broker order status change (filled/partially_filled/canceled/rejected); runs per poll cycle | WEB only | `{type:"fill", execution_id, status:new_status, symbol:row["symbol"], filled_qty:order.filled_qty, account_id?}` | `await notify(kind="fill", title=f"{row['symbol']} {new_status}", body=f"Execution {execution_id}: {order.filled_qty} shares {new_status}", severity="success" if new_status=="filled" else "info", account_id=str(account_id) if account_id else None, ref_type="execution", ref_id=execution_id, action_url=f"/executions/{execution_id}", data={"execution_id":execution_id,"status":new_status,"symbol":row["symbol"],"filled_qty":order.filled_qty,"account_id":account_id}, push=True)` | **YES** |
| 12 | **Exit engine dispatched** | `trading/exit_engine.py:433` (web, always after auto or propose dispatch) `trading/exit_engine.py:360` (mobile, auto-exit only) | Exit engine decides to auto-execute or propose a sell for an open managed position; line 360 fires only when `exit_mode=="auto"` (after submit succeeds); line 433 fires for any dispatched action | BOTH for auto-exit; WEB only for propose-exit | mobile (auto): title="Trade executed", body="SELL {qty} {symbol} @ mkt [{mode}]", data={type:"execution_alert",symbol,side:"sell",qty,strategy_id,execution_id} / web (always): {type:"alert",source:"exit_engine",rule_name:"Exit: {strategy_id}",action:{type:"exit",mode,decision,symbol,reason,qty,strategy_id,detail}} | **⚠ ORCHESTRATOR DECISION — see Notes.** Replace BOTH lines with one `await notify(...)` call at line 433 location: `await notify(kind="exit", title=f"Exit: {symbol} — {decision.action} [{exit_mode}]", body=f"SELL {exit_qty} {symbol} — {decision.reason}", severity="success" if exit_mode=="auto" else "info", strategy_id=row["strategy_id"], ref_type="position", ref_id=str(position_id), action_url=f"/positions/{position_id}", data={"symbol":symbol,"decision":decision.action,"exit_mode":exit_mode,"reason":decision.reason,"qty":exit_qty,"strategy_id":row["strategy_id"],"detail":decision.detail,"execution_id":ref if exit_mode=="auto" else None}, push=exit_mode=="auto")` | **YES** |
| 13 | **Auto-entry buy executed** | `trading/proposal_intake.py:225` | `auto_execute_entry()` completes a buy submit via `ExecutionService.submit()`; fires immediately after submit succeeds | MOBILE only | title="Trade executed", body="BUY {qty} {symbol} @ mkt [{mode}]", data={type:"execution_alert",symbol,side:"buy",qty:int,strategy_id,execution_id} | `await notify(kind="execution", title=f"BUY {args['qty']} {args['symbol']} @ mkt [{result.execution_mode}]", body=f"Auto-entry: {strategy_id} bought {args['qty']} {args['symbol']}", severity="success", strategy_id=strategy_id, ref_type="execution", ref_id=result.execution_id, action_url=f"/executions/{result.execution_id}", data={"symbol":args["symbol"],"side":"buy","qty":int(args["qty"]),"strategy_id":strategy_id,"execution_id":result.execution_id})` | **YES** |
| 14 | **Session node exit prompt** | `signals/session_node_strategy.py:326` | `_push_exit_prompts()` detects an exit condition for an open session-node managed SPY position; deduped via `claim()` / `forget()` idempotency guards | MOBILE only | title="SPY exit prompt ({prompt.kind})", body=prompt.message, data={type:"exit_prompt",symbol:SYMBOL,position_id:row["id"],kind:prompt.kind} | `await notify(kind="exit", title=f"SPY exit prompt: {prompt.kind}", body=prompt.message, severity="warning", ref_type="position", ref_id=str(row["id"]), action_url=f"/positions/{row['id']}", data={"symbol":SYMBOL,"position_id":row["id"],"kind":prompt.kind})` | No (signals/) |
| 15 | **Momentum rebalance complete** | `signals/momentum.py:567` | Monthly rebalance finishes all buys + sells; `complete_run()` then `update_strategy()` then this push | MOBILE only | title="Momentum rebalance", body="Bought {nb}, sold {ns}, holding {nh} · ~${deployed:,.0f} [{mode}]", data={type:"rebalance_summary",strategy_id,month,bought,sold,held,deployed_usd,execution_mode} | `await notify(kind="rebalance", title=f"Momentum rebalance complete: {sid} {month}", body=f"Bought {nb}, sold {ns}, holding {nh} · ~${float(deployed):,.0f} [{mode}]", severity="success", strategy_id=sid, group_key=f"rebalance:{sid}:{month}", data={"strategy_id":sid,"month":month,"bought":nb,"sold":ns,"held":nh,"deployed_usd":float(deployed),"execution_mode":mode})` | No |
| 16 | **Momentum rebalance abandoned** | `signals/momentum.py:407` | Rebalance run has been stuck in "executing" for > MAX_REBALANCE_AGE_SECONDS (persistent order failures); `complete_run()` clears the run | MOBILE only | title="Momentum rebalance incomplete", body="{sid} {month}: orders kept failing — rebalance abandoned...", data={type:"rebalance_incomplete",strategy_id,month} | `await notify(kind="rebalance", title=f"Momentum rebalance abandoned: {sid} {month}", body=f"{sid} {month}: orders kept failing — rebalance abandoned, retries next month.", severity="warning", strategy_id=sid, group_key=f"rebalance:{sid}:{month}", data={"strategy_id":sid,"month":month,"status":"abandoned"})` | No |

---

## Raw call-site count

| Function | Raw sites (production, excl. tests + notify.py internals) |
|---|---|
| `market_hub.broadcast_alert(` | 8 |
| `send_alert_push(` | 12 |
| `send_proposal_push(` | 1 |
| **Total** | **21** |

---

## NOTES / RISKS

### `trading/` sites — MUST get adversarial Sonnet review before commit
Every change in this list touches the execution or notification path for real money:

| File | Lines | Event |
|---|---|---|
| `trading/execution_service.py` | 191 | Execution submitted (cockpit broadcast) |
| `trading/exit_engine.py` | 360, 433 | Exit engine (mobile push + web broadcast) |
| `trading/fill_tracker.py` | 168 | Fill status update |
| `trading/proposal_intake.py` | 225 | Auto-entry buy executed |
| `trading/proposals.py` | 327, 347 | Trade proposal (web + mobile) |

Review focus: verify that replacing `broadcast_alert`/`send_alert_push` with `await notify(...)` cannot block the execution path on failure (the existing try/except isolation must be preserved around the notify call), and that no field drops that downstream tests or the mobile app depend on.

### Orchestrator decisions needed before Composer can implement

**A. Proposal push (row #9 — `trading/proposals.py:327,347`)**
`send_proposal_push` sends APNs with `category: "TRADE_PROPOSAL"` — this is what gives the iOS notification its interactive Approve/Reject buttons (UNNotificationActionIdentifier wired in the mobile app). `notify()` calls generic `send_alert_push`, which uses no category, stripping the interactive buttons.

Options:
1. **(Recommended)** Call `notify(push=False)` to handle DB persist + WS broadcast, then keep existing `send_proposal_push(row)` + `start_proposal_live_activity(row)` calls unchanged. Zero mobile regression, WS event type changes for the bell.
2. Extend `notify()` with a `push_fn` override parameter — adds complexity to the unified emitter.
3. Add a `proposal_push: bool` flag to `notify()` that routes internally to `send_proposal_push` — tight coupling.

**Decision needed:** confirm option 1 (push=False + keep specialized push calls) is acceptable.

**B. Exit engine double-push risk (row #12 — `trading/exit_engine.py:360,433`)**
When `exit_mode=="propose"`, `create_phone_proposal()` (called on line 383) already calls `send_proposal_push` internally (via `trading/proposals.py`). If the exit engine `notify()` also fires `push=True`, the founder gets two mobile pushes for the same exit-propose event.

Recommendation: `push=exit_mode=="auto"` — mobile push only for auto-exits; propose-exits rely on the proposal push.

**Decision needed:** confirm this is correct, or confirm propose-exits should also get a separate "exit proposed" push.

**C. Rule-triggered content (rows #1 and #2 — `api/main.py:269,326,407`)**
NOTIFICATIONS_FOUNDATION.md requires "specific + human" titles, not "rule triggered". At the call sites, `item["rule_name"]` is available but the `action` payload is an opaque dict that varies by rule. The proposed title `f"{item['rule_name']} triggered — {item['action'].get('type','?')}"` is a reasonable default, but for rules with descriptive names (e.g. "SPY below VWAP") this reads well; for rules with generic names it degrades.

No blocker — proceed with the pattern; orchestrator can refine titles per rule-type in a later pass.

**D. Execution push (row #10 — `trading/execution_service.py:191`)**
Currently WEB only. The proposed call uses `push=True`, adding mobile push that didn't previously exist. This is consistent with NOTIFICATIONS_FOUNDATION.md ("fill / execution → both after notify()"), but it is a new behavior the founder should be aware of. If execution-submitted is too noisy on mobile (auto-entry fires this immediately before the auto-entry push from `proposal_intake.py:225`), set `push=False` on this site and only push from the fill-tracker when status reaches "filled".

**Decision needed:** push=True or push=False for execution submitted (line 191)?

**E. `notify()` is async — all emit sites must use `await notify()` or `notify_soon()`**
All current sites are inside `async def` functions and `await` their push calls, so `await notify(...)` is the right replacement throughout. No sync wrappers needed.

---

## POST-BUILD STATUS (2026-06-29)

**1b-1 (non-trading, 11 sites)** + **1b-2 (trading, 7 sites)** BUILT via Composer, reviewed, gate-green
(ruff + 1010 pytest). `notify()` hardened to never raise into its caller (persist wrapped → `return {}`).
**Adversarial Sonnet review of 1b-2: SAFE TO COMMIT** — all execution-safety invariants clean (path untouched,
no double/missing push, interactive proposal push + Live Activity intact, push flags correct, `db_path` threaded).
One backend MINOR fixed: `proposals.py` `ref_id=str(row["id"])`. Backend Phase 1b is COMPLETE in the working
tree — NOT committed (waits for 1c) and NOT deployed.

### ★ Phase 1c scope is BIGGER than "the bell" — adversarial review caught it
The migration changes the WS event from `{"type":"alert"/"execution"/"fill", ...}` to
`{"type":"notification", "kind":..., ...}`. **THREE** frontend WS consumers guard on the OLD types and will
silently drop events until updated — 1c must fix ALL three (atomic deploy with 1b):
| Consumer | Old guard | Breaks (until 1c) | Fix |
|---|---|---|---|
| `frontend/components/workspace/notification-bell.tsx` | `type:"alert"` | the bell (known) | consume `type:"notification"` + `GET /api/notifications` + server read-state |
| `frontend/lib/use-execution-monitor.ts:64` | `msg.type !== "execution"` | live execution/fill toasts (CRITICAL) | accept `type:"notification" && kind in {execution,fill}`; read flat row / `data` |
| `frontend/lib/workspace/use-alert-stream.ts:45` | `type !== "alert"` | exit/proposal alert-stream updates (MAJOR) | accept `type:"notification"` with the right `kind`s |
Also: `market_hub.recent_alerts()` deque + `GET /api/alerts/recent` stop receiving migrated events → 1c replaces
that endpoint with `/api/notifications`. (Mobile APNs push is UNAFFECTED — it still fires for all sites.)

## DEPLOY COUPLING reminder

`broadcast_alert()` sends `{"type": "alert", ...}` on the WebSocket.  
`notify()` sends `{"type": "notification", ...}` on the same WebSocket.

The **current web bell subscribes to `type:"alert"`** messages. Once Phase 1b reroutes any emit site through `notify()`, that event becomes a `type:"notification"` message the old bell ignores (silent drop).

**Phase 1b (reroute emits) + Phase 1c (update web bell to consume `type:"notification"`)** must ship as a single atomic deploy. Do not merge 1b alone.

The `market_hub.recent_alerts()` deque (50-item in-memory list, served by `/api/alerts/recent`) will also stop receiving entries for migrated events. Phase 1c must replace that endpoint with `/api/notifications` before the bell UI breaks.
