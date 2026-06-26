# Decision-engine model artifacts

The trained, committable outputs of the offline backtest+calibration harness (`research/train.py`). The live
scorer (`signals/`, increment 3) loads these to turn a setup's point-in-time features into a **calibrated
win-probability + Expected Value + confidence band**.

Files (produced by `python -m research.train --labeled-db data/research/labeled.db`):
- `model.json` — logistic score model: `intercept`, `coefficients`, `feature_names`, `preprocessing`
  (scaler means/stds + numeric medians). The transparent factor weights.
- `calibrator.json` — Platt (sigmoid) calibration `{A, B}` fit on out-of-fold predictions.
- `ev_table.json` — per calibrated-probability bucket: win-rate, Wilson 95% CI, avg win/loss, EV,
  and an `insufficient_data` flag.
- `meta.json` — `n_samples`, `base_rate`, `brier`, reliability table, `data_source`, and an `honesty`
  block (`low_confidence` until the sample reaches ~1,000–10,000 setups).

## `strategy_stats.json` — validated per-strategy backtest stats (the honest "conviction")
Separate from the generic scorer above. Because the generic 22-feature scorer is **edgeless** (Brier ≈ base
rate), a strategy's proposal should carry **its own validated backtest stats** as the conviction number, not a
generic per-symbol score. Serv loader: `signals/strategy_stats.py`; when present, a strategy proposal's `score`
comes from here (takes precedence over the generic scorer — display/context only, never read by RiskEngine).
`get_agent_status` also surfaces each strategy's `backtest` block. Produced OFFLINE on the PC; **gitignored →
force-add it.** Shape:
```jsonc
{
  "as_of": "2026-06-26",
  "source": "Norgate survivorship-free S&P500 Current&Past, 2010-2026",
  "strategies": {
    // KEY = the LIVE strategy id serv uses (dip_buy | dip_refined | earnings_drift | session_node_spy)
    "dip_buy": {
      "label": "Dip-Buy",
      "win_rate": 0.558,            // fraction 0..1
      "ev_pct": 0.59,              // EV per trade, percent (point estimate)
      "ev_ci_pct": [0.38, 1.02],   // 95% CI on EV/trade (block-bootstrap); null if NOT significance-tested
      "n_setups": 21462,
      "per_year_positive": "16/17",// or null
      "validated": "validated",    // "validated" | "strong-unverified" | "exploratory"
      "method": "block-bootstrap CI excludes 0; OOS train/test confirmed",  // or null
      "note": "healthy>SMA200 + oversold + market-down; exit -3/+3 sell-half/trail/20d"
    },
    "earnings_drift": {
      "label": "Earnings Drift", "win_rate": 0.572, "ev_pct": 0.89,
      "ev_ci_pct": null,           // significance test pending → leave null, don't fabricate
      "n_setups": 6624, "per_year_positive": "16/17",
      "validated": "strong-unverified", "method": null,
      "note": "pre-earnings drift; blowout adds ~+0.19%/trade; needs block-bootstrap"
    }
    // session_node_spy: include as "validated":"exploratory" (no edge), or omit entirely.
  }
}
```
Key by the LIVE strategy id (so serv matches `rule_id`); `validated` is honest; `ev_ci_pct` is the EV/trade CI
(null until `significance.py` runs). Commit: `git add -f research/artifacts/strategy_stats.json`.

## Commit policy (important)
**`*.json` here is gitignored.** Dev artifacts (trained on free Alpaca IEX, which is survivor-biased + a few
symbols) are throwaway and must NOT be mistaken for the real model.

The **real** artifacts are produced OFFLINE on the founder's Windows PC against **Norgate** survivorship-free
data, then committed deliberately:
```
git add -f research/artifacts/model.json research/artifacts/calibrator.json \
            research/artifacts/ev_table.json research/artifacts/meta.json
```
Only commit artifacts whose `meta.json` `data_source` is the survivorship-free Norgate run.
