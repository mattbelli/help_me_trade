# REGIME_SLEEVES_SPEC — paper-only regime-local sleeves (PC → serv build spec)

*2026-07-03. Companion to `TACTICAL_SLEEVES_SPEC.md` (same handoff pattern). Source evidence:
`References/EDGE_HUNT_FINDINGS.md` Round 5 + `research/HYPOTHESIS_LEDGER.csv` H82/H83/H86.
Backtest scripts (PC-only, gitignored): `data/research/_edgehunt/generals_dip.py`,
`v_bounce_harvester.py`, `vix_term_snap.py`.*

## ⚠ Evidence standard — read first
These sleeves are **REGIME-LOCAL**: qualified on 2021–2026 (mostly last-24m) evidence under a
founder-directed fork of the research process. They are NOT tier-1 validated edges and must
**never** appear in `strategy_stats.json` as `validated` or ship a bare win% number. They run
**paper-only, propose-only**, carry an explicit Tier-B label, and each has a **pre-registered
kill line** (below) that must be enforced mechanically, not by memory. Decay is expected and
handled; that's the design. Every proposal goes through the decision journal so calibration
accrues honestly.

Card labeling rule: `win_prob = null`; show instead a `regime_stats` block:
`{window: "2025-06..2026-06", excess_ev_pct, ci, n, note: "regime-local evidence — expected
finite lifetime; kill line armed"}`. Numbers come from the committed stats below — the LLM
never generates them.

---

## Sleeve A — generals_dip (daily-flow slate feeder)

**Thesis (regime):** index concentration + conditioned dip-buying → 3-day flushes in the
mega-cap leaders get bought while the name is in an uptrend.

**Universe (point-in-time, refresh monthly):** top-30 symbols by 60-day average dollar volume
(close × volume), computed from the production daily-bar store (the ~500-name momentum/S&P
feed), excluding ETFs (SPY etc.). Recompute at each month-end; applies to the following month.

**Signal (evaluated at each close T):** `close > SMA50` AND `3-day return ≤ −3.5%`.
**Proposal:** generated after the close of T; **entry = next open (T+1)**. **Exit = T+11 open**
(10 trading-day fixed hold — no stops/targets/trails; fixed-hold is deliberate).
**Frequency expectation:** ~25 setups/month across the top-30 (multiple can fire same day —
cap concurrent positions at 5; rank same-day candidates by 3d-return depth, most-oversold first).
**Sizing:** small/pilot; paper account.

**Committed stats for the card (as of 2026-07-02, h=10d, excess over SPY, net 10bps):**
last-12m: +3.63%/trade, CI [+0.11, +4.66], p=0.021, wr 60%, n=399. Full 2021–26: +0.14% (ns) —
show the honest pair, not just the good number. Per-year trend monotone improving since 2021.
Caveats to display: "last-12m leans on the 2026 recovery leg; strategy was negative in
2021–2022 tape."

**KILL LINE (mechanical, checked at each month-end):** trailing-6-month realized excess
(paper fills vs SPY same-window) < 0, **or** trailing-60-trade win rate < 45% → auto-disable
the sleeve + notify. Log the check result to the journal monthly either way.

## Sleeve B — vol_event (episodic, ONE sleeve with three candidate triggers)

**Thesis (regime):** modern vol shocks terminate fast; buying *confirmed termination* captures
the V. The existing `vix_spike` sleeve (TACTICAL Phase 2) is trigger 1 of this family — do not
run it as a separate strategy; consolidate here.

**Triggers (paper-trade all three side by side; tag every proposal with `trigger_id`):**
1. `vix_spike` (existing): VIX close > 28 AND ≥8 of prior 12 days VIX < 22. Exit: VIX < 20 or
   60td cap. (Unchanged from TACTICAL spec.)
2. `drawdown_stab`: SPY close ≥5% below its rolling 20d-high close AND VIX ≥ 25 AND
   stabilization day (SPY close > prior close AND low > prior low; first qualifying day only).
   Entry T+1 open. Exit: next-day open after SPY close recovers to the trailing 20d-high level
   that defined the drawdown, capped at 30 trading days.
3. `term_flip`: VIX/VIX3M ratio > 1.0 (close basis) for ≥2 consecutive days, then first day
   back ≤ 1.0 → entry T+1 open. Exit: fixed 21 trading days.

**Instrument:** SPY only. **Cluster cap:** max 1 active vol_event position at a time; a new
trigger firing while a position is open is journaled as "suppressed (cluster cap)" — that
record is the side-by-side comparison data.
**Promotion rule (pre-registered):** after ~5 completed episodes per trigger, promote the best
realized trigger to primary and demote the others to journal-only.

**Committed stats for the card:** trigger 2 post-2020: 8/10 wins, median +6.2%/episode;
trigger 3 post-2020 (21d hold): 13/14 wins, mean +5.0% vs +1.4% unconditional baseline.
Episodic small-n — display as descriptive, never as a win% probability.

**KILL LINES:** trigger 2 — any episode MAE ≤ −15%, or 3 consecutive losing episodes →
disable trigger. Trigger 3 — mean 21d return < +2.0% over the next 10 episodes → suspend
trigger. Trigger 1 — per TACTICAL spec.

## New data dependency (serv build item)

**VIX3M daily pull:** FRED series `VXVCLS`, keyless CSV:
`https://fred.stlouisfed.org/graph/fredgraph.csv?id=VXVCLS` (columns
`observation_date,VXVCLS`; '.' = missing, skip). Add to the existing FRED regime refresh
(same loop as VIXCLS/DGS10/DGS2), store as `macro` series `vix3m`. `term_flip` cannot fire
without it. PC reference copy: `data/research/vix3m_fred.csv` (2007-12→2026-07).

## Plumbing checklist (serv)

- [ ] `vix3m` in the FRED refresh + freshness check (stale >3 trading days → trigger 3 dormant, not erroring).
- [ ] Both sleeves: **propose-only, paper, force-created DISABLED** (session-node pattern); phone card carries the Tier-B `regime_stats` block + caveats.
- [ ] `trigger_id` + `sleeve=regime_local` tags on every journal entry.
- [ ] Mechanical kill-line checks (monthly for A, per-episode for B) with auto-disable + notification.
- [ ] **Dormancy visibility** (per SETUP_AVENUES.md): when nothing fires, the slate says why
      ("generals_dip: 0 flushes in top-30 today; vol_event: no active vol episode").
- [ ] Never write these into `strategy_stats.json`.
