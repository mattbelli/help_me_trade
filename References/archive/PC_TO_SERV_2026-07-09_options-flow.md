# PC → serv handoff: Options-Flow Screener (Phase 1 complete, pick up Phase 2)

*Written 2026-07-09 by the PC/orchestrator (Opus) after an overnight sprint. Branch
`feat/options-flow-screener` off `origin/main`. Phase 1 is built, reviewed, and live-verified; this
doc points a serv agent to everything and scopes Phase 2 (which belongs on serv — it needs the runtime).*

## TL;DR
A watchlist-scoped **OPRA attention router** — surfaces unusual options activity as honest,
deterministic facts to help the founder trade the **underlying stock**. **Mode A perception, never a
scored signal.** Phase 1 (EOD unusual-volume + queryable resource + UI + backfill) is done. Phase 2
(intraday polling + blocks + **convergence watch + phone notifications**) is next and should be built
here on serv. Phase 3 (streaming/sweeps) is **gated on usage data — do not build it yet**.

## Read these first (authoritative)
- **`research/fable/2026-07-09_options-flow-screener-design.md`** — the full design (Fable). §5 = the
  detection rules + every constant; §6 = the event shape + perception integration; §7 = the phased
  plan; §3 = the honesty pitfalls (give them full weight); §8 = open probes.
- `References/PERCEPTION_LAYER.md` / `References/SETUP_AVENUES.md` §2 — the Mode-A / tier framing.
- This doc for status + Phase-2 scope.

## Non-negotiable invariants (preserve in every future phase)
- **Read-only. No order path.** The only trading-API call is the read-only `/v2/options/contracts`
  (OI metadata). `trading/execution_service.submit()` is untouched; no-short intact. Run the
  `trading-safety-adversary` on any PR that touches `signals/push`-adjacent notify budgets (Phase 2).
- **No `win_prob`/`ev`/`confidence` anywhere** — enforced by schema shape (`db/options_flow.py`,
  the event dict, the `/api/options-flow` payload). Direction is a 3-value label
  (`buy_side|sell_side|undetermined`) with a citation, never a probability.
- **`reason`/`caveat` are deterministic template strings** built by code; the LLM narrates from them.
- **Serv-only heavy loops, env-gated, fail-soft** (the PC order-flow-RAM incident is the reason).

## What's DONE + verified (8 commits on this branch)
| Area | Files | Notes |
|---|---|---|
| Foundation | `signals/options_flow/{constants,__init__}.py`, `db/options_flow.py`, `brokers/alpaca.py` (+read-only `get_option_bars`/`get_option_contracts`/`get_option_chain_snapshot`) | events + daily-baseline stores, pruned (180d/400d) |
| Detection | `signals/options_flow/detect.py` | UV-1 (vol/OI) + UV-2 (DTE-bucketed z-score); EOD direction=`undetermined` |
| Resource | `ai/perception/resources.py` | `RESOURCES["options_flow"]` + `flow` alias; `read`+`summarize` |
| EOD job | `api/main.py` `_options_flow_eod_once` + `register_job` | **env-gated OFF** `OPTIONS_FLOW_ENABLED=0`; after-close, once/ET-day, fail-soft, **pull-only (no notifications yet)** |
| Backfill | `signals/options_flow/backfill.py` | bars→`options_flow_daily` + writes `research/options_flow/thresholds.md`; **live-verified: SPY 768 contracts/7,787 rows** |
| UI | `api/main.py` `GET /api/options-flow`, `frontend/.../options-flow-widget.tsx`, `frontend/lib/api.ts`, `widget-registry.tsx` | caveat-forward workspace widget |
| Tests | `tests/test_options_flow_{data,detect,scan,backfill}.py` | **60 hermetic tests green; ruff + frontend typecheck clean** |

## Ground-truth data facts (live-probed — build on these, don't re-derive)
- **Broker access:** options data is **Alpaca-only** → use `get_broker("alpaca")`, NOT bare
  `get_broker()` (which resolves the active broker = Schwab and no-ops). This bit us; it's fixed in the
  scan + backfill. Reuse the same accessor in Phase 2.
- **Historical trades:** `GET /v1beta1/options/trades?symbols=<OCC,csv>&start=&limit=` (query form;
  the path form 404s). Depth ~Feb 2024 (~2.5yr, one bull regime).
- **Historical quotes DO NOT EXIST** on Alpaca → direction can only be classified **at ingest time**
  (live/polled quote alongside the print). Backfill tunes volume/premium baselines only.
- **OI:** `/v2/options/contracts` returns `open_interest` as a **STRING**, **lags 1–3 days**
  (`open_interest_date`), and is `None` for illiquid strikes (`max(oi,1)` guard already in place).
- **Snapshots carry NO daily bar** (P-4 confirmed) → active-set ranking needs a bars/trades call.
- **Streaming:** `wss://stream.data.alpaca.markets/v1beta1/opra`, **msgpack only**, subscribe by exact
  OCC (no wildcards), ≤~1,000-contract cap. Probe P-1 (below) not yet run — Phase 3.

## Deploy Phase 1 on serv (when the founder authorizes the merge to main)
1. Merge branch → `main` (founder authorizes). It lands **dormant** (env-gated off).
2. Seed baselines: `python -m signals.options_flow.backfill --symbols <flow watchlist>` (needs Alpaca
   creds; writes `research/options_flow/thresholds.md` — set `ZSCORE_STD_FLOOR` in
   `signals/options_flow/constants.py` from its recommendation, currently placeholder `0.0`).
3. Enable: `OPTIONS_FLOW_ENABLED=1` (optional `OPTIONS_FLOW_INTERVAL_SECONDS`, default 1800). The EOD
   scan then records events nightly; query via the `options_flow` resource or `GET /api/options-flow`;
   the widget shows them. **This starts the usage clock that gates Phase 3.**

## Phase 2 — the next build (belongs on serv; needs the runtime to test)
Per design §7 Phase 2 + §6(b). Ships:
- **60s intraday poll loop** (market hours): active-set refresh (snapshots, top-N by size, ≤~1,500
  contracts), trades cursor-pull (`next_page_token`), quote co-fetch for candidate events.
- **BLK-1 blocks** + intraday UV-1; **DIR-1 aggressor tagging** (quote rule, `direction_basis=
  "polled_quote"`, honest `undetermined` bucket, ~83% class accuracy citation — Savickas-Wilson 2003).
- **Filters F-1 (hygiene condition codes, fetch/cache `/meta/conditions/trade`), F-2 (spread
  suppression — ~30-40% of volume is multi-leg), F-3 (0DTE exclusion for index/mega-caps).**
- **Convergence watch** (`signals/options_flow/convergence.py`): flow event + RVOL≥1.5 + level-touch
  (session node / price alert) → **≥2 components → one `notify()`** under a daily push budget (design
  §6b, `CONV_*` constants). Reuse `signals/notify.py` + the RVOL/session-node machinery. **This is the
  connect-the-dots payoff — the phone ping.** Solo flow events push only above a large premium.
- **OI daily refresh** (`get_option_contracts` pre-open) for vol/OI.
- Run `trading-safety-adversary` on the PR (touches notify budgets).

## Phase 3 / 4 — do NOT build yet
Phase 3 (msgpack websocket + SWP-1 sweep/split clustering + at-print DIR-1) and Phase 4 (forward-return
stamping + quarterly usage/hit-rate eval) are **gated**: build Phase 3 only if a quarter of Phase-1/2
usage shows the founder actually acts on flow alerts. This is the founder's pre-committed rule (§8).

## Open probes (read-only, extend `scripts/opra_probe.py` / `opra_probe2.py`)
- **DONE:** P-2 (OI on contracts ✓ — string/lagged), P-4 (snapshot has no dailyBar ✓).
- **TODO (market hours):** P-3 — live-trade lag + condition-code frequency counts (validates F-1/F-2
  thresholds). **TODO (Phase 3):** P-1 — websocket schema + practical subscription cap + whether the
  options stream contends with the equities stream connection.

## Loose ends / nits (not blockers)
- **UV-2 reason string says "calls"** but baselines sum all volume regardless of right —
  reword to "options" (`detect.py::_reason_uv2`) or split baselines by right.
- `research/options_flow/thresholds.md` on the PC is from a **short smoke-window** (SPY/AAPL, weeks) —
  re-run the full backfill on serv for representative percentiles.
- Pre-existing `ruff` F401 in `tests/test_api_broker.py` (unused import) — **not ours**, left as-is.
- The per-phase Composer briefs live on the PC (`tools/composer-bridge/briefs/of_phase1*.md`) — the
  design doc is the source of truth, but they show how each phase was scoped if useful.
