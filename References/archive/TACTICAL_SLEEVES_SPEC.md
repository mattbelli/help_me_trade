# Spec for serv — Phases 1 & 2: the tactical sleeves that complete the combined book

*PC→serv handoff. The combined book = 3 strategies sized **70/20/10**: momentum (Phase 3, separate spec) +
**steep-YC dip (20%, Phase 1)** + **VIX-spike (10%, Phase 2)**. These two are the **tactical sleeves** — they
deploy during downturns and are what **cut the book's drawdown from ~24% (momentum alone) to ~16.5%**. That
drawdown protection is the "step away and don't stress" payoff, even though the alpha lives in momentum. Both fit
the existing **per-symbol** framework (unlike momentum) — they're small builds. Both ship **full-auto, paper-fenced**.
They share one new dependency: a production FRED/regime data pipeline. Build order: pipeline → Phase 1 → Phase 2.
Independent of the momentum build (different data), so serv can slot them whenever — recommend after Phase 3.*

## Shared dependency: production FRED/regime pipeline
The yield curve and VIX live only in `data/research/regime.db` (research-only). Production needs them at scan time.
- Add a daily refresh loop in `api/main.py` (analog of `_earnings_refresh_loop`) that fetches from FRED:
  **DGS10** (10y), **DGS2** (2y), **VIXCLS** (VIX) into a production regime table `regime_macro(series, date, value)`.
  Reuse `research/regime.py::fetch_fred` — just point it at the production DB.
- Add `market_regime(as_of) -> {term_spread: y10-y2, vix: ...}` reading the prod table (mirror
  `research/regime.py::market_regime`, prod DB).
- **Staleness guard:** FRED lags ~1 day and can fail. If the latest regime value is older than N days (e.g. 5),
  the gated strategies must **not fire** (fail-closed — no regime data = no trade), and log it. Never trade on a
  stale/guessed regime.

## Phase 1 — Steep-YC dip (20% sleeve)  [~0.5 day]
The validated standout, and it's nearly the existing `dip_buy` strategy:
- **Entry:** existing dip entry (`c>SMA200 ∧ stock −5%/3d ∧ SPY −1.5%/3d`, via `is_strategy_entry`) **AND**
  `market_regime(today).term_spread > 0.5`.
- **Recommended shape:** add an optional `yc_min_spread` **param to the existing `dip` kind** (default `null` =
  no gate, so `dip_buy` is unchanged), rather than a whole new kind. Then **seed a new strategy `steepyc_dip`**
  using the `dip` kind with `params.yc_min_spread = 0.5`. Less duplication; `dip_buy` untouched.
- **Exit:** the existing dip **10-day fixed hold** — already supported, no new exit code. (The −3%/+3% trail
  *destroys* the edge per research — do not use it.)
- **Sizing/mode:** its own `notional_usd` (≈20% of book capital), `entry_mode='auto'`, `alert_style='per_trade'`
  (only ~16–20 fills/yr — per-trade alerts are fine, no summary needed). Paper-fenced. Seed disabled + unbound.
- **Honest framing in strategy_stats:** this is the **high-beta tactical** sleeve. Beta-adjusted, ~half its raw
  +0.88%/trade excess is beta-recovery; standalone selection alpha is +0.47%/trade and not significant. Credit it
  as tactical exposure, NOT standalone alpha. (We learned this from the Jensen-alpha check — don't re-inflate it.)

## Phase 2 — VIX-spike reversion (10% sleeve)  [~1–2 days]
SPY-only, "buy fear." Fits the per-symbol loop with a single-symbol universe (like `session_node_spy`):
- **Entry (a new `vix_spike` kind):** `market_regime` VIX close > 28 **AND** ≥8 of the prior 12 trading days had
  VIX < 22 (a *sudden* spike from calm, not chronic high-vol). No open position in this sleeve. Instrument: **SPY long.**
- **Exit (new):** add `evaluate_vix_exit()` to `signals/exit_evaluators.py` — exit when VIX close < 20 (reversion)
  OR 60-trading-day max hold. Add the `vix_spike` branch to `trading/exit_engine.py::_strategy_kind()` and
  `_process_managed_position()`. (Current evaluators only do stop/target/trail/time/calendar — this is a new
  conditional-on-regime exit.)
- **Sizing/mode:** its own `notional_usd` (≈10% of book), `entry_mode='auto'`, `alert_style='per_trade'`. Fires
  ~0.6×/yr (rare). Paper-fenced. Seed disabled + unbound.
- **Honest framing:** +3.3%/episode but **n=10 in 16 years** — a thin-sample pilot. Opportunistic overlay only,
  small notional; never a primary sleeve.

## Safety (both)
- Per-symbol, fit the existing scan + chokepoint — **zero changes to `execution_service`/`risk_engine`**. Every
  order still flows kill-switch → `RiskEngine.validate()` → `submit()`.
- Paper fence: both ship `entry_mode='auto'`, honored only in dry_run/paper (the existing 3-layer gate). Never live.
- **Adversarially review** the new `exit_evaluators.py` + `exit_engine.py` branches (trading-path) and the FRED
  loop before commit. The FRED fetch is new network I/O — must fail-closed (stale/failed fetch ⇒ no trade), never
  block the scan loop, never fire on missing data.
- Decimal-only for money/qty.

## Verify
- FRED pipeline: DGS10/DGS2/VIXCLS land in the prod regime table daily; `market_regime` returns correct
  term_spread + vix; staleness guard blocks trading when data is >N days old.
- Phase 1: `steepyc_dip` fires only when term_spread > 0.5; reuses the dip 10d-hold exit; `dip_buy` behavior
  unchanged (no gate). Paper integration: bind to a paper account, force a qualifying dip in a steep-curve window
  → confirm auto submit through RiskEngine, managed-position, per-trade push, 10d exit.
- Phase 2: `vix_spike` fires on a spike-from-calm, holds SPY, exits on VIX<20 or 60d; exit engine handles it.
- Pre-push: `ruff check .` + `pytest -q` + frontend typecheck.

## Completing the book
With momentum (Phase 3, 70%) + `steepyc_dip` (20%) + `vix_spike` (10%) all enabled at the right notionals and
bound to paper accounts, you have the validated combined book. **Cash-when-idle is automatic** — when the dip/VIX
sleeves aren't firing, their capital just sits uninvested in the account (do NOT auto-park it in SPY; the idle-SPY
variant tested *worse*: −0.06 Sharpe, +5pp drawdown). Paper-validate the whole book together before trusting it.
