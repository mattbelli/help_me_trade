# Tactical-sleeve validation fixtures (Phases 1 & 2)

Golden fixtures so serv can validate its **steep-YC dip** (Phase 1) and **VIX-spike** (Phase 2) trigger/gate
logic against a known-correct reference — the same de-risk that made momentum's Gate-1 bit-exact. Unlike the
momentum bars (licensed Norgate, out-of-band only), **these are FRED data (VIXCLS, DGS10, DGS2) — public and
freely redistributable**, so they ship via git.

## Files
- **`tactical_regime_series.json`** — the input series: `{date, vix, y10, y2, term_spread}` daily, 1990→2026
  (VIX calendar). Source: FRED `VIXCLS`, `DGS10`, `DGS2` (via `regime.db`).
- **`vix_spike_triggers.json`** — the 14 golden VIX-spike episodes (entry date + vix, exit date + reason).
- **`steep_yc_gate.json`** — golden steep-curve dates (`term_spread > 0.5`): 5,705 steep days + 142 gate
  open/close transitions.

## How serv validates (two steps, mirrors momentum's Gate-1)
1. **Algorithm correctness (bit-exact):** run your trigger/gate logic on `tactical_regime_series.json` and confirm
   it reproduces the golden outputs exactly:
   - **VIX-spike:** entry when `vix_close > 28` AND `≥8 of the prior 12 trading days had vix < 22` AND no open
     position; exit when `vix_close < 20` OR 60 trading-day max hold. → must reproduce all 14 episodes in
     `vix_spike_triggers.json` (entry date, exit date, exit reason).
   - **Steep-YC gate:** `term_spread = DGS10 − DGS2 > 0.5`. → must reproduce the steep-day set / transitions in
     `steep_yc_gate.json`. (The *dip entry* itself — price>SMA200, −5%/3d, SPY −1.5%/3d — is already validated as
     `dip_buy`; Phase 1 only adds this gate, so validating the gate dates is sufficient.)
2. **Data correctness:** confirm your own FRED fetch (`VIXCLS`/`DGS10`/`DGS2`) matches the series values here. VIX
   and treasury yields are clean single-value public series — far lower artifact risk than the momentum stock
   data (no SNDK/CRH-class hazards), so a close match is expected. Any boundary disagreement (e.g. vix 27.9 vs
   28.1 on a trigger day) is a data-vintage difference, not a logic bug.

## Data-quality note (worth knowing)
The yield curve has been **hovering right at the +0.5 threshold in 2026** — the gate flips OPEN/CLOSE within days
(e.g. 2026-04-30 OPEN → 2026-05-04 CLOSE → 2026-05-18 OPEN). That's expected for a hard threshold near the current
spread, not a bug: the dip sleeve checks the gate daily, so it simply fires on steep days and is dormant on flat
days. (Consistent with the research finding that 0.3–0.5 is the optimal band and 0.6+ loses significance — the
curve genuinely lives near this boundary now.) No hysteresis is needed for paper; note it if flicker becomes
operationally annoying.
