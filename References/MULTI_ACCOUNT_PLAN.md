# Multi-account architecture — plan & spec (2026-06-21)

*Durable spec for making the app fully multi-account: multiple broker accounts as first-class across data,
execution, risk, sync, and UI — and trading strategies assignable to a specific account. Founder decision
(2026-06-21): **full multi-account first** (not the lightweight per-strategy binding), targeting **all of it**
— multiple Alpaca paper accounts AND multiple Schwab accounts, execution-capable where allowed.*

*Companion: `HANDOFF.md` (current focus), `BUILD.md` (roadmap), the dip-strategy forward-test (`signals/
strategy_scan.py`) which becomes account-bound in Phase C. This refactors the **sacred execution path** — every
change under `trading/` and to risk caps gets adversarial Sonnet review before commit.*

## Goal / end state
- Multiple accounts coexist: e.g. a main Alpaca paper account + a dedicated forward-test Alpaca paper account +
  the user's Schwab Individual/Roth/Joint.
- Portfolio, positions, executions, proposals, and the dashboard are **account-aware** (queryable/filterable by
  account).
- The execution chokepoint routes each order to the **correct account**, and **RiskEngine caps are enforced
  per-account** (never globally weakened).
- A **strategy is assignable to an account** (Phase C); the dip-buy forward-test binds to the dedicated paper
  account and auto-executes there (Phase D).

## Core model — Connections → Accounts (the crux of "all of it")
The two brokers have fundamentally different credential shapes; the model must hold both:
- **Alpaca:** one API key pair == one account. A 2nd Alpaca paper account == a 2nd key pair. Creds are
  **per-account**.
- **Schwab:** one OAuth app (api_key + app_secret + callback + token) exposes **many** account hashes. Creds are
  **shared at the app level**; accounts differ by `account_hash`.

So we model **two levels**:

1. **Broker connection** — a credentialed link to a broker.
   `{ id, broker ("alpaca"|"schwab"), label, enabled, credentials (broker-specific blob), token_state, created_at }`
   - Alpaca connection ≈ one key pair (→ exactly one account).
   - Schwab connection ≈ one OAuth app (→ N accounts).

2. **Account** — a tradeable/observable account under a connection.
   `{ id, connection_id, broker, label, broker_external_id (alpaca account.id | schwab account_hash),
      enabled, execution_capable (bool), is_default (bool, per broker), created_at, last_synced_at }`
   - `execution_capable`: Alpaca paper = true; Schwab may be **read-only** initially (false) — live Schwab
     execution stays gated. A read-only account can sync/show portfolio but **RiskEngine hard-blocks any order**
     to it.

**Reconcile with existing `trade_accounts`** (`db/trades.py`): it already has `{id, label, broker,
broker_external_id, last_synced_at}` and is referenced by `trade_events(account_id, external_key)`. **Extend
`trade_accounts` into the Account table above** (add `connection_id`, `enabled`, `execution_capable`,
`is_default`) rather than introducing a parallel table — the ledger already keys trades by it, so unifying
avoids a second source of truth. Add a new `broker_connections` table for the connection level.

## DB schema changes
**New:** `broker_connections` (see fields above; credentials stored like the current `brokers.*` settings blob,
masked on read; gitignored `.env`/settings as today — NEVER commit creds).

**Extend `trade_accounts`** → the canonical Account table (+ `connection_id`, `enabled`, `execution_capable`,
`is_default`).

**Add `account_id TEXT` to** (the single-account-assumed tables identified in recon):
- `poll_runs` (`db/sqlite.py`) — snapshots become per-account. Key by `(account_id, captured_at)`; keep
  `broker` for convenience. (account_snapshots/position_snapshots/order_snapshots inherit via `poll_run_id`.)
- `order_executions` (`db/order_executions.py`) — explicit `account_id` column (audit + per-account counts).
- `paper_order_proposals` (`db/paper_orders.py`) — explicit `account_id` column (today broker is only inside
  `proposal_json`).

**Indexes:** `(account_id, captured_at)` on poll_runs; `(account_id, created_at, status)` on order_executions
(the daily-count query); `(account_id, status)` on proposals.

`trade_events`/`trade_accounts` already carry `account_id` — no change beyond the Account extension.

## Migration (backwards-compatible, additive)
1. Create `broker_connections`; create a **default Alpaca connection** from the current `brokers.alpaca`
   settings blob. If Schwab is configured, create a Schwab connection from `brokers.schwab`.
2. Upsert Accounts: the current Alpaca account (from the latest snapshot's `account.id`) → one Account under the
   Alpaca connection, `is_default=true`, `execution_capable=true`. Existing `trade_accounts` rows (imported
   Schwab Individual/Roth/Joint) → link to the Schwab connection, `execution_capable=false` (read-only until
   live Schwab is built).
3. Backfill `account_id` on existing `poll_runs` / `order_executions` / `paper_order_proposals` to the default
   Alpaca account (all historical single-account data belongs to it).
4. All columns nullable-then-populated or defaulted so the migration is safe to re-run; behavior unchanged until
   the threading sub-steps land (the default account == today's primary).

## Execution-path threading (account_id everywhere an order flows)
- **`OrderIntent`** (`trading/models.py`): add `account_id: str` (required). It already carries `broker`;
  `account_id` becomes the precise target. Resolve the broker **from the account** at execution time.
- **Proposal build** (`trading/order_proposal.py` `build_order_proposal_from_args`): accept optional
  `account_id` (default = the default execution-capable account for the resolved broker). Stop calling
  `primary_broker()` as the implicit account pointer — resolve via the Account registry.
- **`create_phone_proposal` / `paper_order_proposals`**: persist `account_id`.
- **Approval-time validation** (`trading/proposals.py` `approve_proposal`): re-resolve the account, confirm it
  still exists + is `execution_capable` + enabled, BEFORE `ExecutionService.submit()`. (Fixes the
  recon's "broker resolved at proposal time, could drift by approval" seam.)
- **`ExecutionService.submit`**: resolve the adapter from `intent.account_id`'s connection (not
  `get_broker(primary)`); write `account_id` on the execution record.

## RiskEngine per-account hardening — SAFETY-CRITICAL (never weaken a cap)
**Guiding invariant:** making a cap per-account must only ever make it *equal or stricter*, never remove a
ceiling. Each item below gets an explicit test asserting the per-account limit still blocks, AND adversarial
review.
- **Snapshot lookup** (`risk_engine.py` uses `get_latest_snapshot(intent.broker)`) → `get_latest_snapshot(
  account_id=intent.account_id)`. A missing per-account snapshot must **fail closed** (block), exactly as today.
- **Buying power / per-order value / concentration** — computed against the **target account's** snapshot
  (cash, buying_power, portfolio_value, positions). Concentration % is per-account (correct: a position in
  account A must not gate account B).
- **`count_executions_today`** (`db/order_executions.py`) — add `account_id` filter. **This is the highest-risk
  change** (today it's a global counter with no filter): a per-account daily cap must still enforce; verify the
  query can't silently return 0 and uncap. Decide explicitly: is `max_daily_orders` per-account (likely yes) —
  document it.
- **Open-orders cap** — per-account (from the target account's snapshot open_orders).
- **Read-only accounts:** RiskEngine hard-blocks any order whose account is `execution_capable=false`
  (deny-by-default; a new check near the top of `validate`).
- **Kill switch** stays global (one big red button over everything) — unchanged.
- Risk caps stay **global defaults** for now; **per-account overrides** are a later enhancement (note, don't
  build yet).

## Snapshot + sync (per-account)
- **`get_latest_snapshot`** (`db/sqlite.py`): primary signature becomes `get_latest_snapshot(account_id=...,
  )` (keep a broker fallback shim for callers during transition; remove once threaded).
- **Poll/sync loop** (`trading/sync.py`, `api/main.py` poll loop): iterate **enabled accounts**; for each,
  fetch its snapshot via its connection's adapter and write `poll_runs.account_id`. Alpaca: one account per
  connection. Schwab: iterate the connection's account hashes (the recon notes `_schwab_transaction_to_event`
  already threads `account_id`; extend the *snapshot* sync to iterate, mirroring the ledger).
- **Broker factory** (`brokers/factory.py`): cache adapters by **connection id** (not broker name), since two
  Alpaca connections have different creds. `get_broker_for_account(account_id)` resolves connection → adapter.

## Settings / config
- Move broker creds from the fixed `brokers.{alpaca,schwab}` blob to the `broker_connections` table (settings
  still the entry UI; secrets masked on read, same as today).
- New API routes: list/create/update/delete connections + accounts; trigger per-account sync; set default
  account. Mask secrets on read; validate creds on save (test connection).

## Phase A — COMPLETE (2026-06-22) + carry-forward findings
All 5 sub-steps shipped, each adversarially reviewed, behavior-identical for the single default Alpaca account
(commits a65a370 · b876264 · fb9fd4b · bf75577 · + sub-step 5). **Carry-forward to Phase B** (latent, inert
today with one account/broker; must be handled when a 2nd Alpaca connection exists):
- **Per-connection credentials + broker-factory cache keyed by connection id** (not broker name) — the actual
  enabler for a 2nd Alpaca paper key. The `get_broker_for_account` seam is in place; wire creds here.
- **id-equality guard at per-account sync**: assert the registered account's `broker_external_id` == the fetched
  snapshot's `account.id` before stamping `poll_runs.account_id` (fail-closed on mismatch) so a connection's
  snapshot can't be routed to the wrong ledger account. Inert at single-account; required once connections fan out.
- **Schwab snapshot iteration**: `SchwabAdapter` hardwires one `account_hash`; add an optional per-call
  `account_hash` (or per-connection adapter instances) to iterate hashes. Also: don't mark a Schwab account
  `is_default` until this lands (would mis-stamp the many-hash connection with one ledger id).
- The poll loop currently iterates brokers; switch to iterating **enabled accounts** once connections exist.

## Phase B sub-step 1 — DONE (2026-06-22): per-connection Alpaca credentials + 2nd account live
A 2nd Alpaca paper account ("STRAT1") is wired end-to-end: `APCA_*_STRAT1` env → `discover_alpaca_credential_
suffixes` → `_register_suffixed_alpaca_connections` registers `conn_alpaca_strat1` + `acct_alpaca_strat1`
(execution-capable, non-default; secrets stay in env, only `{"env_suffix":"STRAT1"}` in the DB) → factory builds
a per-connection adapter (`get_broker_for_connection`, cache keyed by connection id) → `get_broker_for_account`
routes orders to the right creds → poll loop iterates enabled Alpaca accounts (`_poll_account`) with a
fail-closed id-guard (snapshot `account.id` must match the account's `broker_external_id`). Default account
byte-identical. Adversarially reviewed. Test-isolation fixed (conftest strips ambient `.env` suffix vars +
clears broker caches). **MUST-FIX before STRAT1 actually TRADES:** (1) ~~execution-reconcile per-account~~ **DONE (C.0)** —
`fill_tracker` + the `list/count` fns take `account_id`; each account reconciles its own executions via its own
adapter; `_poll_account` reconciles per-account; boot reconcile per-account. Byte-identical default + None
paths; adversarially reviewed. (2) **rules/alerts per-account — RESOLVED BY DESIGN, no work needed:** rules are
watchlist-global and run ONLY on the default account's poll (no double-fire); per-account portfolio rules are a
future enhancement only if wanted. Still open: **M-2 migration-stamp hardening** (stamp the default's
`broker_external_id` from a primary-key snapshot, not just latest) before any DB rebuild.

## Phase B — Frontend account-awareness (summary; full brief later)
- Account selector (global context, like the current snapshot provider) → drives portfolio/positions/proposals
  views per account; an "all accounts" rollup.
- Settings UI: manage connections + accounts (add a 2nd Alpaca paper key; connect Schwab; mark read-only).
- Proposal/execution surfaces show which account.

## Phase C — Strategy registry + account binding (summary; the "Strategies" settings section)
- Generalize the single `dip_strategy_*` flag into a **strategy registry**:
  `{ id, name, enabled, account_id (target, or "all eligible"), params (notional, entry/exit), exit_mode
  ("auto"|"propose") }`.
- `run_strategy_scan` iterates enabled strategies; each proposes into its **bound account** (notional sizing
  against that account's snapshot).
- Settings → **Strategies** section: list, toggle, edit params, assign account, choose exit-mode. Dip-buy is
  strategy #1. **Strategies stay isolated, independently toggleable, FROZEN/versioned modules** (founder
  principle) — the learning layer observes outcomes but never mutates a strategy.

## Phase D — Exit module (summary; the forward-test completion)
- Per-account, per-strategy **position-state tracking** (entry price, high-water mark, half-sold flag, current
  stop, entry date, strategy id) for strategy-opened positions.
- Exit rules: −3% stop / +3% sell-half→breakeven / trail ~10–12% / 20-day cap (the validated config).
- Executes per the strategy's `exit_mode`: **auto** (through the now-account-aware chokepoint, sells-reduce-
  long only, kill-switch-respected) for the forward-test account; **propose** pushes each exit to phone.
- Known v1 limitation to also address: a user-rejected entry can re-propose next cycle → add a per-symbol
  cooldown.

## Phase A sub-steps (build order; each = tests + adversarial review before commit)
1. **Schema + migration** (additive, behavior-preserving): `broker_connections` table, extend `trade_accounts`,
   add `account_id` columns + indexes, backfill migration. No flow changes yet — default account == today's
   primary. ← **first coder brief.**
2. **Account registry + factory** : Account/Connection accessors; `get_broker_for_account`; factory cache by
   connection. Still single default account in use.
3. **Thread `account_id` through proposal → intent → execution** (no risk-semantics change yet): persist +
   carry account_id; approval-time account validation. Reviewed.
4. **RiskEngine per-account hardening** (the safety-critical step): per-account snapshot/buying-power/
   concentration/daily-count/open-orders; read-only hard-block. **Heaviest adversarial review.**
5. **Per-account snapshot + multi-account sync**: poll loop iterates accounts; `get_latest_snapshot(account_id)`
   threaded; Schwab snapshot iteration.

(Phases B/C/D follow once A is solid and the forward-test account can be created + synced + executed against.)

## Risk register (from recon — watch these)
- `count_executions_today()` global → per-account: must not uncap (CRITICAL).
- Buying-power/concentration reading the wrong account's snapshot (HIGH).
- Read-only (Schwab) account must hard-block execution (HIGH).
- Broker/account drift between proposal and approval → validate at approval (MEDIUM-HIGH).
- Migration backfill must assign all legacy rows to the default Alpaca account (correctness).
- Secrets: connections hold creds — mask on read, never commit (as today).
