# Adversarial Delta Audit — Money Path, 07-02 → 07-06
*Fable 5 audit · read-only · 2026-07-06 · delta 07-02→07-06*

Scope: everything that touched `trading/`, `db/order_executions*`, risk/auth in the window — safety-M1
quarantine (`33ca3c3`), #41 done_for_day (`64123b4`), #33 caps/breaker/paper-guarantee (`64f5db5`),
#34 auth (`bd3d0fe`), mobile-35 proposal lifecycle (`4142b09`/`78bc363`/`6afa2a0`), sync H5 (`e55ab8a`).
Every candidate finding below was run against the live code with an attempt to refute it first; still-open
items already carded in `References/board.json` (orphan-repair-surface, #39 re-baseline, bad-tick M3,
per-strategy caps, device-bound approval, M2 kill-switch-blocks-exits) are **not** re-reported.

## 1. What actually holds (verified, credit where due)
- **Paper-endpoint guarantee (#33/C1) is real.** The check is on the *same adapter object* that places the
  order (`trading/execution_service.py:140–146` → `aplace_order` at `:203`), `is_paper_endpoint` is a
  property of the adapter's own `_base_url` (`brokers/alpaca.py:107–109`), non-Alpaca adapters fail the
  `getattr(..., None) is not True` test, and the reject path clears notional + pages critically. No
  check-vs-place drift window exists. (Softness in the *substring* itself → M2 below.)
- **The C2 TOCTOU fix is sound as shipped.** The per-loop submit lock (`execution_service.py:27–43,95`)
  plus the documented no-await tail (`risk_engine.py:500–506` through the sync notional write at
  `execution_service.py:107–114`) is loop-atomic; I traced the tail and found no `await` between the first
  sum read (`risk_engine.py:512`) and validate's return. The breaker's awaited notifies all sit *before*
  the sums or on immediate-return fail paths.
- **#34 fail-closed mutations + asymmetric kill switch verified.** `verify_mutation_token` refuses all
  mutating verbs when `API_TOKEN` is unset (`api/auth.py:85–92`, wired in `api/middleware.py:30–31`);
  `PUT /api/settings` hard-400s `kill_switch_active` and admin-gates any `trading` patch — including the
  non-dict-`trading` trick (`api/main.py:1694–1704`); `POST /api/kill-switch` needs admin only to *disarm*
  (`:1722–1726`). No bypass found on the `trading.*` surface.
- **#41 shipped version handles partial fills.** `done_for_day` branches on fill progress
  (`trading/fill_tracker.py:54–60`), partials map to `partially_filled` → attribution fires
  (`position_ledger.py:136` accepts it), the row *stays polled* (`db/order_executions.py:370` includes
  `partially_filled`), and TIF is always `"day"` (`execution_service.py:199`) so zero-fill→`expired` can't
  strand a live GTC order. The re-fire guard (`fill_tracker.py:297–302`) prevents per-tick re-push.
- **No double-reconciler in-app**: only Schwab gets the account-less `_poll_broker`; Alpaca is per-account
  only (`api/main.py:414–426`), so the documented single-poller assumption holds in prod.
- **Sync H5 fix** (`trading/sync.py:88–134`): Decimal throughout, abs-amount-weighted price, zero-net
  groups now log instead of vanishing. Correct.

## 2. Ranked findings

### HIGH

**H1 — A partial fill whose order jumps straight to `canceled`/`expired` is NEVER attributed and NEVER
quarantined — the exact silent orphan safety-M1 was built to kill.**
`record_strategy_fill` gates on `status in {"filled", "partially_filled"}` (`trading/position_ledger.py:136`)
and returns `None` *quietly* for everything else — before any quarantine logic can run. But the reconciler
maps broker `canceled`/`expired` directly to those terminal statuses regardless of `filled_qty`
(`fill_tracker.py:54–60`, only `done_for_day` branches on fill progress), then calls attribution with that
status (`:321–327`). So: strategy BUY partially fills and is canceled/expires within one 20s poll gap — or,
far more likely, **during a deploy restart or overnight downtime** (deploys kill MainPID at arbitrary
times; the intermediate `partially_filled` observation is simply missed) — and the poll wakes to
`submitted → expired, filled_qty=70`. Real shares are held, no `managed_positions` row, no
`fill_attribution_orphans` row, no critical page. The exit engine walks only `managed_positions`, so the
position is unmanaged indefinitely. The team *did* think of this case for the phone push —
`_fill_report_outcome` maps canceled/expired-with-fills to `"partial"` (`fill_tracker.py:84–88`) — but not
for attribution. Fix is small: in `record_strategy_fill`, treat any terminal status with `filled_qty > 0`
as attributable (open/refresh the managed row for the filled shares), or at minimum quarantine it.

**H2 — `partially_filled` rows escape BOTH #33 notional sums, so the in-flight reservation and the daily
notional cap under-count by the full notional of every partially filled order.**
`sum_inflight_notional` counts `('pending','submitted')` only (`db/order_executions.py:280`);
`sum_today_notional` counts `('pending','submitted','filled')` (`:308`). Neither includes
`partially_filled` — yet #41 made that a *long-lived* status (a working limit order sits there from first
partial until terminal, potentially hours; Alpaca reports `partially_filled` as a live status). The moment
a $40k order partially fills, its entire $40k vanishes from the buying-power reservation
(`risk_engine.py:542–545`), the per-symbol concentration in-flight add (`:612–616`), and both daily
notional caps (`:512–533`) — while the remainder is still working at the broker and the filled shares are
not yet in the ≤5-min-stale snapshot. A canceled-after-partial escapes `sum_today_notional` *permanently*
(status `canceled`) even though real shares were bought. The intent was clearly inclusive — the sibling
open-orders counter `count_submitted_executions` DOES include `partially_filled` (`:539`), and the
`notional_usd` column is never cleared on the transition (`fill_tracker.py:307–314`). One-line fix per
function: add `'partially_filled'` to both status lists (and consider counting filled-portion notional for
terminal partials in the daily sum).

### MEDIUM

**M1 — The safety-M1 quarantine is not crash-durable: a restart between the terminal status write and the
attribution call loses the fill forever, with no recovery scan.**
`reconcile_open_executions` persists the terminal status first (`fill_tracker.py:307–316`) and calls
`record_strategy_fill` after (`:321`). Kill the process between the two (deploys do exactly this) and the
row is now `filled` — out of the reconcile pool (`db/order_executions.py:370`) — so attribution never
re-runs, and since `record_strategy_fill` never executed, no orphan row exists for
`flush_fill_attribution_orphans` to page (`position_ledger.py:274–320` only pages *existing* rows; grep
confirms no scan anywhere for filled strategy BUYs lacking a managed row). The 07-02 audit's M1 remedy
explicitly included "reconcile managed rows from broker positions"; the shipped fix covers
attribution-*resolution* failures durably but not this ordering seam. Cheap fix: a periodic sweep of
today's `filled`/`partially_filled` BUY executions with `proposal_id` strategy-parseable and no matching
open managed row / orphan row → quarantine. (This same sweep closes H1.)

**M2 — #34's admin scope is keyed to the top-level `"trading"` key only; the `"brokers"` surface (endpoint
URLs + keys) needs just the normal mutation token, and the paper guarantee behind it is a substring check.**
`api/main.py:1694` admin-gates `if "trading" in patch` — a patch like
`{"brokers": {"alpaca": {"api_base_url": ..., "api_key_id": ...}}}` sails through with the ordinary
`API_TOKEN` and takes effect immediately (`clear_broker_cache()` at `:1708`). The backstop is
`is_paper_endpoint = "paper-api" in self._base_url` (`brokers/alpaca.py:109`), which a normal-token holder
can satisfy with a URL that *contains* the substring but doesn't *mean* it (a proxy, or a path like
`https://api.alpaca.markets/paper-api/../` that the origin normalizes away). Today's blast radius is
bounded — no live Alpaca keys exist in the system — but the same non-admin surface includes
`data_base_url`, i.e. a normal token can re-point the quote feed that `_live_market_price` and sizing
consume. Two cheap hardenings: parse the URL and require host `== "paper-api.alpaca.markets"` exactly;
add `brokers` to the admin-gated key set.

### LOW

**L1 — `record_open_position`'s upsert overwrites `qty` with the latest cumulative `filled_qty`**
(`db/managed_positions.py:95–113`), so the managed row's qty can be stale-low between partial observations
(growth while `partially_filled` is skipped by the re-fire guard, `fill_tracker.py:297–302`) or wrong-high
after an exit-then-late-fill sequence. Consequence is blunted because the exit engine deliberately sizes
from the *live broker snapshot*, never `row["qty"]` (`trading/exit_engine.py:243–246`) — but per-strategy
attribution/analytics read the ledger qty.

**L2 — `get_proposal(...) is None` → silent unattribution** (`position_ledger.py:160–162`). By the module's
own deny-by-default doctrine ("when we cannot prove a fill is unmanaged-by-design, surface loudly") a
*missing* proposal row for a filled BUY should quarantine, not `return None`. No current deletion path
exists (retention is allowlisted to poll tables, `db/retention.py:23–34`), so this is a latent seam, not a
live bug.

**L3 — Concurrent per-account reconcile tasks can double-page the same orphan**: each `_poll_account` task
calls `_flush_attribution_orphans`; two tasks can both read `status='open'` and both `await notify` before
either marks notified (`position_ledger.py:285–319`). Consistent with the stated "duplicate page beats a
permanently lost one" stance — noting it so nobody "fixes" it by marking before notifying.

## 3. Verdict
The window's headline safety work is genuinely good — the paper-endpoint check is on the right object, the
submit-lock/no-await invariant actually closes the C2 TOCTOU it claims to, #34 has no bypass on the
`trading.*` surface, and #41 shipped the reviewed fill-progress version, not the rejected alias. But both
new fill-side guarantees have the same blind spot: **they only fire on transitions the poller happens to
observe.** H1 (terminal-jump partials skip attribution *and* quarantine) and M1 (status-write/attribution
crash seam) mean a strategy BUY can still end up held with no exit management and no page — the precise
outcome safety-M1 was shipped to make impossible — and H2 means the #33 notional guarantees are weaker
than their name by the size of every partially filled order. One periodic sweep (terminal BUYs with
`filled_qty>0` and no managed/orphan row) plus two one-line status-list additions closes all three.

**Counts:** CRITICAL 0 · HIGH 2 · MEDIUM 2 · LOW 3.
