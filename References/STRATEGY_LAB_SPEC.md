# STRATEGY LAB — build spec (PC → serv)

*Founder request 2026-07-16: "a GUI to show visualizations of strats … exactly what we've been looking at,
broken down year by year or even quarter by quarter recently — a line each for SPY and Russell returns and a
line each for our strat returns."*

Mission fit: the dashboard is a **cockpit to supervise the agent and review what it did/knows** — this page is
how the founder reviews the *research book* (validated sleeves vs their benchmarks) without asking an agent to
regenerate a PNG. Read-only, no execution surface.

## Split of work
- **PC (done, this commit):** `research/strategy_lab_export.py` generates committed JSON artifacts under
  `research/artifacts/strategy_lab/` (force-added past the artifacts gitignore, same pattern as `model.json`).
  The research DBs are PC-only and gitignored, so serv never computes — it only renders.
- **serv (this brief):** one API route file + one frontend page reading those JSONs. Open the board card when
  work starts, per board discipline.

## Data contract (already committed — build against these)
`research/artifacts/strategy_lab/benchmarks.json`:
```json
{ "generated": "2026-07-16",
  "series": { "SPY": [["2010-07-20", 100000.0], ...],
              "IWR": [...], "MDY": [...], "IWM": [...] } }
```
`research/artifacts/strategy_lab/<sleeve_id>.json` (one per sleeve; initial two:
`earnings_drift_sp.json`, `earnings_drift_midcap.json`):
```json
{ "id": "earnings_drift_sp",
  "label": "Earnings drift — S&P 500 C&P",
  "generated": "2026-07-16",
  "config": { "slots": 10, "fraction": 0.10, "cost_pct": 0.20,
              "universe": "S&P 500 Current & Past", "n_trades": 6624 },
  "caveats": [ "Backtest, not live performance",
               "Linear-accrual marking — live drawdowns will run deeper",
               "Fair-shuffle mean over 10 orderings" ],
  "metrics": { "cagr": 0.1896, "max_drawdown": 0.2345, "sharpe": 1.33,
               "avg_invested": 0.478, "per_trade_mean_pct": 0.89, "win_pct": 57.2 },
  "equity": [["2010-07-20", 100000.0], ...] }
```
Daily points, $100k start, ISO dates, ~4k–6k points per series (fine as one fetch). Annual/quarterly returns
are **derived in the frontend** from the equity series — do not add them to the contract.

## Backend (serv) — `api/strategy_lab_routes.py`
FastAPI router (register in `api/main.py` beside `strategy_router`):
- `GET /api/strategy-lab` → list of sleeves: `[{id, label, generated, config, caveats, metrics}]`
  (everything except `equity`), plus `{"benchmarks": ["SPY","IWR","MDY","IWM"]}`.
- `GET /api/strategy-lab/{id}` → the full sleeve JSON. 404 unknown id.
- `GET /api/strategy-lab/benchmarks` → benchmarks.json verbatim.
Implementation: read + cache the JSON files from `research/artifacts/strategy_lab/` at startup (they change
only on git pull; a mtime check per request is enough). No DB, no auth changes, no write endpoints.

## Frontend (serv) — `/strategy-lab` page
New route `frontend/app/strategy-lab/page.tsx` + sidebar entry (icon: FlaskConical) after Research.
Components under `frontend/components/strategy-lab/`, charts in **recharts** (already a dep, v3.8):
1. **Equity curves** (`LineChart`): one line per selected sleeve + per selected benchmark (checkbox toggles,
   default: both sleeves + SPY + IWR). Y log-scale toggle (default ON — that's how slope=growth reads).
2. **Period returns** (grouped `BarChart`): granularity toggle **Year | Quarter**; Year shows the full
   window, Quarter shows a trailing-N selector (default 12). Bars grouped per period across the same
   selected series, colors matching the line chart, zero-line drawn.
3. **Metrics strip**: per sleeve — CAGR, maxDD, Sharpe, avg % invested, n trades, and **excess CAGR vs SPY**
   computed from the series. Benchmarks get CAGR/maxDD only.
4. **Honesty banner (non-negotiable):** the sleeve's `caveats` render as a persistent amber strip on every
   view — this page must never read as live performance or calibrated forecasts. No win%/EV framing beyond
   the raw historical `win_pct` labeled "historical".
Derivations: annual = calendar-year resample of equity; quarterly = quarter-end resample; first partial
period included and labeled (e.g. "2010*"). Series are different lengths — align by date, no forward-fill
across a series' end.

## Verify (serv)
- `curl /api/strategy-lab` lists 2 sleeves; `/{id}` payload matches the committed JSON.
- Page renders both sleeves + SPY/IWR; 2020 bar for the S&P sleeve ≈ +73%; 2026 quarterly bars show mid-cap
  (IWR + mid sleeve) leading. Log toggle, granularity toggle, and series toggles all work.
- Caveat banner visible on load. `npm run typecheck` green.

## Later (not this round)
- Workspace widget wrapper (widget-registry) once the page settles.
- More sleeves as they validate (residual momentum, dip, VIX — export from the combined-book fixture).
- Live-vs-backtest overlay once the paper sleeves produce a journal (the calibration loop's natural home).
