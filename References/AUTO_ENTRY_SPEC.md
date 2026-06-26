# Spec for serv — per-strategy paper auto-entry + execution alerts

*PC→serv handoff. The founder (decision-maker) wants to toggle individual strategies into full autonomy
(auto-execute entries), **paper-fenced**, with a phone alert on every fill. This is level-3 of the autonomy
ladder on the ENTRY side, mirroring the auto-EXIT that already ships. serv owns this build (live-trading-path
code on `main`); PC wrote the spec. Branch off `main`, build, adversarially review the `trading/` diff, PR for
the founder to merge.*

## What already exists — do NOT rebuild
- **Auto-EXIT already works in production.** `strategies.exit_mode` (`auto`|`propose`); `trading/exit_engine.py`
  calls `ExecutionService.submit()` directly when `auto` (with the in-flight marker / snapshot-lag / 300s
  double-sell defenses). `dip_buy` ships `exit_mode="auto"`.
- **The chokepoint needs ZERO changes.** `trading/execution_service.submit()` + `RiskEngine.validate()` are the
  authority for every order regardless of caller (kill-switch, qty bounds, snapshot freshness ≤5min, buying
  power, per-order value cap, concentration cap, open-orders + daily-orders caps; `live` hard-blocked).
- **Entry today is always propose.** `signals/strategy_scan.py::_run_strategy()` unconditionally calls
  `proposal_intake.propose_trade()` → `create_phone_proposal()` → APNs. We add an `auto` branch alongside it.
- Push infra (`signals/push.py`: APNs/Expo, `send_alert_push`) is complete and proven.

## Changes (all on `main`, ~150 lines)

1. **Schema — `db/strategies.py`**: add `entry_mode TEXT NOT NULL DEFAULT 'propose'` to the `strategies` table.
   Idempotent `ALTER TABLE` migration in `init_strategy_tables()`; expose in `_row_to_strategy()`; accept +
   validate (`{propose, auto}`) in `update_strategy()`. Mirrors the existing `exit_mode` exactly.

2. **Entry dispatch — `signals/strategy_scan.py::_run_strategy()`**: branch on `strategy.entry_mode`.
   - `propose` (default): unchanged → `propose_trade(candidate)`.
   - `auto`: refactor the sizing + pre-checks currently inside `proposal_intake.propose_trade()` (price lookup,
     qty-from-notional, buying-power cap, `RiskEngine` pre-check) into a **shared helper**; call it, build the
     `OrderIntent`, call `ExecutionService.submit(intent)` directly, then open the managed-position row (item 3).
   - Apply the existing per-strategy gates (`max_concurrent_positions`, `max_deployed_usd`) BEFORE auto-submit,
     same as the propose path. Decimal-only for money/qty.

3. **Managed-position on auto-entry — `trading/position_ledger.py`**: `record_strategy_fill()` currently
   **requires a `proposal_id`** to open the `managed_positions` row. Auto-entry has no proposal → without a fix
   the position is untracked and the exit engine is blind to it. Add a path that accepts `strategy_id` directly
   when `proposal_id` is absent, so the auto-entered position is tracked + managed/closed normally.
   **This must not ship without it.**

4. **"Trade executed" phone push**: today `submit()` only broadcasts a WebSocket `execution` event — no APNs.
   After a successful `submit()` on the auto paths (the NEW auto-entry AND the existing auto-exit in
   `exit_engine.py`), call `send_alert_push(title="Trade executed", body="BUY N AAPL @ mkt [paper]",
   data={"type":"execution_alert", ...})`. **Recommend pushing at the two auto call sites, not inside `submit()`**
   — manually-approved proposals already get their own proposal-flow notifications, so call-site pushes avoid
   double-notifying. (serv's call.)

5. **API + frontend**: `api/strategy_routes.py::api_update_strategy()` accept `entry_mode`;
   `frontend/components/settings/settings-strategies-panel.tsx` add the `entry_mode` select next to `exit_mode`.

6. **★ Safety gate (non-negotiable) — paper-only fence**: server-side, honor `entry_mode="auto"` **only when**
   `trading_config()["execution_mode"] in ("dry_run","paper")` (mirror the short-selling restriction). If
   execution mode is `live`, auto-entry **reverts to `propose`** — it must never auto-fire a live order. The
   typed double-gate stays the sole path to live; "auto paper" can never become a live backdoor. Also keep
   `session_node` strategies blocked from `auto` entry (as they are from auto exit).

## Safety invariants (unchanged — restate in the PR)
- Every auto order still flows kill-switch → `RiskEngine.validate()` → `ExecutionService.submit()`. Nothing
  skips the chokepoint.
- LIVE stays hard-blocked behind the Increment-3 typed double-gate; this feature does not touch it.
- Adversarially review the `trading/` + `signals/strategy_scan.py` diff before committing (standing rule).

## Verify
- Unit: `entry_mode` persists + migrates idempotently; `auto` builds a valid `OrderIntent`; `auto` in `live`
  mode reverts to propose; managed-position row opens on auto-entry; push fires exactly once per auto fill.
- Integration (paper): toggle `dip_buy` to `entry_mode="auto"`, run a scan that fires → confirm order submits
  paper, RiskEngine gated it, managed-position opened, phone got "Trade executed", exit engine later manages/
  closes it and pushes on the auto-exit too.
- Pre-push: `ruff check .` + `python -m pytest -q` + `cd frontend && npm run typecheck`.

## Rollout
Ship behind paper. Founder toggles the on-`main` strategies (`dip_buy`, `dip_refined`, `earnings_drift`) to
`auto` at will. The combined research book (validated on the PC: Sharpe 1.42, ~+5% Jensen alpha, beta 0.76) is
a **separate, later strategy promotion** — not part of this feature.
