# Handoff — "you are here" (2026-06-22)

*Bootstrap for a fresh session. Read this first, then `CLAUDE.md` (mission + working agreements, auto-loaded),
`AGENTS.md` (codebase map), and the per-topic refs: `MULTI_ACCOUNT_PLAN.md` (the active build's full spec +
history), `BUILD.md` (roadmap + dated history), `DECISION_ENGINE_RESEARCH.md`, `SHORT_TRADING_FINDINGS.md`,
`NORGATE_PULL.md`, `ARCHITECTURE.md`, `STARTUP.md` (run + device access), `SERVER_SETUP.md`. Deep server
specifics: the `serv-runtime` memory.*
*Past handoff snapshots (the lineage this file consolidated) live in `HANDOFF_ARCHIVE.md` — when you do a
superseding rewrite of this file, append the old full version there as a new dated section.*

---

## CURRENT FOCUS — multi-account + the dip-buy forward-test (DONE through C.2 + refined-dip, DEPLOYED)
Founder chose **full multi-account** so a strategy can bind to a dedicated account. The whole arc is built,
adversarially reviewed, committed/pushed (`main`), and **deployed live on serv** — **503 tests** green.
Full spec + per-sub-step detail + carry-forward findings: **`References/MULTI_ACCOUNT_PLAN.md`**.

**Model:** two levels — **broker connections → accounts**. Alpaca: one key pair = one account (creds per
account). Schwab: one OAuth app = many account hashes (shared creds). Secrets stay in `.env`; the DB stores only
a non-secret `env_suffix` per connection.

**What's done:**
- **Phase A (accounts first-class)** — schema/migration (`db/multi_account.py`), account read-model
  (`db/accounts.py`), `account_id` threaded through proposal→`OrderIntent`→execution, **RiskEngine per-account
  hardening** (execution-capability hard-block INSIDE `validate` → the chokepoint self-defends; per-account
  daily cap added beside the unchanged global one — never weakened), per-account snapshot + account-keyed
  `get_latest_snapshot`. Byte-identical for the single default account throughout.
- **Phase B.1 — 2nd Alpaca paper account "STRAT1" LIVE.** `APCA_*_STRAT1` env → discovered → `conn_alpaca_strat1`
  / `acct_alpaca_strat1` registered (execution-capable); factory builds a per-connection adapter; poll loop
  iterates accounts with a fail-closed id-guard (snapshot `account.id` must match the account's
  `broker_external_id`). Both accounts sync per-account on serv right now.
- **Phase C.0 — execution-reconcile per-account** (`fill_tracker` + `list/count` fns take `account_id`; each
  account reconciles its own fills via its own adapter). (Rules/alerts stay default-account-only by design —
  watchlist-global, no double-fire.)
- **Phase C.1 — strategy registry + account binding.** `db/strategies.py` (registry + idempotent seed);
  `propose_trade` routes to a bound account (per-account dedup + sizing; `invalid_account` deny-by-default;
  legacy `account_id=None`→default byte-identical); `signals/strategy_scan.py` is registry-driven; `GET/PUT
  /api/strategies`.
- **Phase C.2 — Strategies settings UI + mobile Settings access** (2026-06-22). Cockpit panel
  `frontend/components/settings/settings-strategies-panel.tsx` (per-strategy enable / account-bind /
  notional / exit-mode / `far_support_min`, save-per-card, honesty caption on refined-dip, unbound-while-
  enabled warning). New `GET /api/strategies/accounts` feeds the account picker (`frontend/lib/strategies-api.ts`).
  Settings is now reachable on **mobile** — `<SettingsDialog collapsed />` lives in the phone header
  (gear icon) and the dialog nav stacks responsively (`flex-col sm:flex-row`). Reuses the desktop dialog (no
  duplicate mobile UI — RN is the eventual target). **Pending: in-browser/on-phone visual check.**
- **Per-strategy universe — decoupled from the active watchlist** (2026-06-22): a strategy now scans its OWN
  `universe` (col on `strategies`), not whatever named watchlist is active (that footgun silently repointed live
  strategies). Specs: `sp500` (built-in broad market, `signals/universes/sp500.json` = 503 Alpaca-validated
  constituents), `watchlist:<id>` (pin a SPECIFIC named list), `active_watchlist` (legacy). Resolver
  `signals/strategy_universe.py`; `GET /api/strategies/universes` feeds the UI picker. **Backfill is
  conservative:** existing rows → `active_watchlist` at migration (the live enabled $10k `dip_refined` is NOT
  silently broadened); NEW strategies default to `sp500`; user opts in via the UI Universe dropdown. Alpaca
  verified for breadth: 12k tradable equities available, 500-name × 420d fetch = ~2.5s, well under rate limits.
  Adversarially reviewed: migration atomic, resolver can't raise, no silent broadening.
- **Per-strategy risk caps** (2026-06-22): `strategies.max_concurrent_positions` + `max_deployed_usd`
  (nullable cols, NULL=uncapped; idempotent ADD COLUMN migration). Enforced in `_run_strategy` — the scan
  stops proposing once (open positions + pending proposals) in the bound account hit the cap. This is the fix
  for the **dip-cluster problem** (many names fire on the same down day). Best-effort/advisory at propose-time
  (assumes serialized scans, which the scheduler guarantees); the HARD guarantee stays `RiskEngine.validate()`
  + buying-power at the chokepoint. Adversarially reviewed: no cap-breach path in a serialized scan. Surfaced
  in the Strategies UI as "Max positions" / "Max deployed ($)" (blank = no cap).
- **Refined-dip strategy wired live** (2026-06-22, from `origin/research/decision-engine-data-layer`).
  Ported `research/levels.py` (swing-pivot S/R + `level_features`) + the `far_support_min` arg on
  `is_strategy_entry` (both pure/point-in-time; adversarially reviewed clean). New seeded strategy
  `dip_refined` ("Dip-Buy + S/R filter") = the dip edge PLUS "must be ≥5% above nearest support";
  **seeded DISABLED + UNBOUND**, `far_support_min=5.0`. Also fixed a latent test-only bug:
  `_account_is_ready` now threads `db_path` (was reading the default DB).

**Live state right now:** backend running C.1 code (deploy = `kill <MainPID>` → systemd `Restart=always`
reloads the working tree; migration runs idempotently at boot). Default Alpaca (`acct_fc78907f4bda`) + STRAT1
(`acct_alpaca_strat1`) both polling per-account, no errors. **The `Dip-Buy` strategy is seeded DISABLED**, bound
to STRAT1, $500, `exit_mode=auto`.

**To arm the entry-only forward-test** (when you choose to):
`PUT /api/strategies/dip_buy {"enabled": true}` (already bound to STRAT1). Entries are always **phone-approved**
(safe — Approve/Reject each); `exit_mode` governs EXITS, which **don't fire until Phase D** — so arming now means
approved dip entries accumulate as positions you'd manage by hand until the exit module exists.

## NEXT
1. **In-browser/on-phone check of the Strategies UI** (C.2 shipped but UI-only — verify the panel renders,
   account dropdown lists STRAT1/default, save persists, and the phone gear opens Settings legibly). Then
   decide bindings: refined-dip wants its OWN forward-test account (add `APCA_*_STRAT2` for a clean A/B vs
   dip_buy) — it's seeded unbound on purpose.
2. **Phase D — the exit module** (−3% stop / +3% sell-half→breakeven / trail ~10–12% / 20d cap), auto-executing
   per `exit_mode` through the now-account-aware chokepoint. Completes the round-trip → the forward-test
   measures real EV. Known v1 limitation to fold in: a user-rejected entry can re-propose next cycle (needs a
   per-symbol cooldown).
3. Carry-forward (in `MULTI_ACCOUNT_PLAN.md`): **M-2 migration-stamp hardening** before any DB rebuild; Schwab
   multi-account snapshot iteration (adapter change) if/when Schwab goes beyond read-only. Two low-pri review
   nits: guard `cluster_mean==0` in `research/levels.py`; thread `db_path` into `get_broker_for_account`.

## ⚠️ GOTCHAS — read these
- **Tests must NEVER touch the real `data/trading.db`.** It bit twice this session: the build's Composer/API
  test runs wrote through `DEFAULT_DB_PATH` (= the real DB) — pre-registering STRAT1 (B.1) and arming `dip_buy`
  (C.1). Both caught on post-deploy verification and reset; **zero proposals/trades occurred.** Now guarded:
  `tests/conftest.py` forces `TRADING_DB_PATH` to a throwaway temp DB at import. Still — when reviewing a
  Composer round, **verify the real DB wasn't mutated** (check `strategies`, `trade_accounts`,
  `paper_order_proposals`, `order_executions` for unexpected recent rows) before trusting a deploy.
- **Strategies are isolated, independently toggleable, FROZEN/versioned modules** (founder principle): the
  learning layer may OBSERVE outcomes but must NEVER silently mutate a strategy's validated logic (that would
  destroy the calibrated win%). The registry is config/data only.
- **Deploy w/o sudo:** `kill <MainPID>` (get it via `systemctl show hmt-backend -p MainPID --value`); systemd
  `Restart=always` relaunches into the working tree. Boot runs `migrate_multi_account` + `seed_default_strategies`
  (idempotent). Verify: API responds (HTTP 401 = up, auth-gated), both accounts saving `account=…` snapshots,
  no tracebacks/`mismatch`.
- **`dip_strategy_*` settings keys are DEPRECATED** (the registry supersedes them; the seed read them once for
  carry-over).
- **Free IEX feed = regular hours only**; no options market-data → **options orders are limit-only**.
- **Access:** backend `0.0.0.0:8000` on serv via systemd, reachable on the tailnet (`serv.tail78aab8.ts.net`).
  Plain HTTP over Tailscale's WireGuard only — never port-forward to the public internet. **Local-dev port
  clash:** stop the services before `./dev.sh` (systemd holds `:8000`/`:3001`).
- **LIVE trading is hard-blocked server-side** (`execution_mode` default `dry_run`; flip to `paper` in Settings;
  LIVE needs the typed double-gate, not yet built).

## The validated edge behind Dip-Buy (Norgate survivorship-free backtest)
Entry: **healthy (>SMA200) + oversold (−5%/3d) + market-down (SPY −1.5%/3d)**; exit (Phase D): −3% stop / +3%
sell-half→breakeven / trail ~10–12% / 20d cap. **21,454 setups, ~57% win, honest net ≈ +0.4–0.5%/trade** —
**triple-validated** (alpha-vs-beta ✓, out-of-sample ✓, overlap-aware block-bootstrap significance ✓:
P(mean≤0)=0%). Caveats: GROSS floor thin after costs; ONE 15y regime (2022 negative); significance ≠ forward
guarantee — hence the live paper forward-test. **Short mirror tested → NO edge → no-short invariant stays**
(`SHORT_TRADING_FINDINGS.md`). The dip predicate is reused **verbatim** by the live scan (zero train/serve skew).
Real Norgate harness: `research/strategy_*.py`, `research/significance.py`; runbook `NORGATE_PULL.md`. Still
open: ship real `research/artifacts/*.json` trained on the *strategy* setups (the generic 22-feature model was
edgeless — not shipped).
**Refined-dip (`dip_refined`, far_support_min=5.0):** the dip edge + a "≥5% above nearest support" filter
(`research/levels.py` swing-pivot S/R). Backtest on `origin/research/decision-engine-data-layer` showed the
filter ~**doubled the RECENT excess over SPY (+1.6%→+3%/trade)** — but ⚠️ this refinement is **NOT
block-bootstrap-validated** to the base dip's standard (no significance pass on the variant). Treat as a live
forward-test candidate, not a calibrated edge; the UI says so. The unvalidated branch siblings
(rsi2 / consec_down / momentum entry predicates) are ported into `research/strategy_features.py` but **left out
of the live registry** (no cited edge yet).

## Stable foundation (context)
- **The chokepoint is sacred.** `trading/execution_service.submit()` is the only broker path; `RiskEngine.validate()`
  gates every order (kill-switch-first, deny-by-default, now per-account capability hard-block). Real Alpaca
  **PAPER** for equities + LONG options, full buy-to-open / sell-to-close lifecycle; sells only reduce a held
  long (no shorting). Decimal-only for money in `trading/`. **Adversarially review (Sonnet) any change to
  `trading/` or `db/order_executions` before commit** — it keeps catching real safety bugs.
- **Decision Engine** (the app's #1 value): PERCEIVE→REASON→DECIDE → calibrated win%/EV/CI on every proposal
  (`signals/decision_scorer.py`, shown desktop + phone). Honest by construction (won't fabricate an edge).
  Train-offline/serve-online; research in `DECISION_ENGINE_RESEARCH.md`.
- **R3 phone-approval shipped** (in-app Approve/Reject loop end-to-end through the chokepoint). RN/APNs native
  client is a decoupled parallel track (`mobile-ux-direction` memory).
- **serv** is the dev + runtime home + sole writer of `data/trading.db` (nightly backup → `mattpc`). Do all work
  on serv.

## Other open threads (deprioritized)
- **Layer-A risk profiles** (per-account conservative/moderate/aggressive — config + RiskEngine caps; natural
  fit now that accounts are first-class).
- **RN phone app** (Expo; EAS cloud builds; Mac for APNs/Live-Activities). Lock-screen Approve/Reject = the unlock.
- **Watchlist v3** (`WATCHLIST_SPEC.md`; disliked v2 stashed on serv `stash@{0}`, `serv-parked-watchlist-v2` memory).
- **R1 backfill:** Schwab Realized-G/L import + **futures import** (PDF statements; `/ES /MES /CL` point
  multipliers — completeness gate for the retrospective).

## Working agreements (also in `CLAUDE.md` + auto-loaded memory)
Build via **Composer** (`node tools/composer-bridge/dispatch.mjs --brief <file>`, one run at a time, logs to
`tools/composer-bridge/last-run.log`) — not hand-edits (trivial/surgical excepted). Recon/review via **Sonnet**
subagents. **Adversarially review safety-critical code (execution/risk) before commit.** Review the real `git
diff` + run the gate **yourself**; don't trust self-reports. Founder is **cost-conscious about the Opus main
loop** — delegate heavy work. Cadence: iterate → "happy" → update `BUILD.md`/`HANDOFF.md` → commit + push. Pre-push:
`ruff check .` + `python -m pytest -q` + `cd frontend && npm run typecheck` (all three in CI).
