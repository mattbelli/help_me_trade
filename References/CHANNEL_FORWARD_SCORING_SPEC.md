# CHANNEL FORWARD SCORING — build spec (serv)

*Founder request 2026-07-16: "I see channels on all timeframes… the win is: I draw a channel on a chart live
and we measure its success going forward." Explicitly NOT bar-count exits ("hold N bars" was a prior agent's
wrong frame) — the channel's own geometry defines every exit.*

Mission fit: perception copilot. The founder's eye is the signal source; the agent watches his levels, pings
his phone, and keeps an honest forward ledger of how his channels perform. **No execution path** — pure
perception + journal; nothing here touches `trading/`.

## What already exists (build on, don't rebuild)
- **Channel drawing**: the native chart engine draws trendlines with a `parallelOffset` width handle
  (`frontend/lib/chart-engine/types.ts` — segment + channel leg, drag-editable) on all timeframes, and
  drawings persist via `POST/PUT/DELETE /api/drawings` → `chart_drawings` (`db/chart_features.py`).
- **Trigger loop**: `price_alerts` machinery (repeat/last_triggered_at) + phone push (`signals/push`).
- **Mobile**: `RN_SKIA_CHART_RELAY.md` §5 — the phone renders the same drawings.
- **Intraday bars**: Alpaca SIP minute history verified back to 2016-01 with our keys (probe 2026-07-16);
  1m/5m/1h/4h all aggregate from 1-min. Live quotes already stream.

## The core mechanic — ARM = freeze
Drawing is sketching; **arming** makes it an experiment. When a channel drawing is selected, offer
"Track this channel". That snapshots geometry into a new table — and the snapshot is **immutable**:
editing the drawing afterward does NOT touch the experiment (no silent revisionism). Re-arming an edited
channel closes the old experiment (`status=superseded`, keeps its record) and opens v2.

`db/channel_experiments.py` — `channel_experiments`:
```
id, drawing_id, symbol, timeframe TEXT ('1m'|'5m'|'1h'|'4h'),
anchor_a_ts, anchor_a_price, anchor_b_ts, anchor_b_price,   -- lower line through A→B
width_offset REAL,            -- parallel upper line offset (price units, from the drawing)
extended_hours INTEGER,       -- chart setting at arm time (bar counting must match what the eye saw)
armed_at TEXT, status TEXT ('active'|'closed'|'superseded'|'expired'),
config TEXT JSON              -- overrides of the defaults below
```
**Geometry note (load-bearing):** the engine's time scale is bar-index, not wall-clock
(`CHART_ENGINE.md`), so the line's slope is price-per-BAR on the armed timeframe. The monitor must extend
the line by counting bars of that timeframe (respecting the armed extended-hours setting), not by
extrapolating in calendar time — otherwise weekends/halts bend the line away from what the founder drew.
Line value at bar N: `p_a + slope_per_bar * (bars between anchor_a and N)`.

## Monitor (serv, on bar close of the armed timeframe)
Per active experiment, evaluate the bar against lower / mid / upper line values:
- **LOWER_TOUCH** — bar low ≤ lower + 10% of width (tolerance default; config).
- **MID_HIT / UPPER_HIT** — bar high crosses the line value.
- **BREAK** — bar CLOSE below lower line by >25% of width (config). Channel invalidated → close experiment.
- **EXPIRE** — horizon default 3× the drawn A→B span (in bars), or manual close from the UI.
Every event → `channel_events` (experiment_id, kind, ts, bar_ohlc, line_values) — the raw ledger.

**Paper measurement** (the "did it work" arm, event-based exits only):
- LOWER_TOUCH while flat → open sim position at the NEXT bar's open (honest fill; same convention as the
  research pipeline's next-open fix).
- Exit half at MID_HIT, remainder at UPPER_HIT; hard stop on BREAK (fill = break bar close); EXPIRE closes
  at last close. One position per experiment at a time; re-entries on later touches allowed and journaled
  as separate rounds. Rows → `channel_trades` with entry/exit prices, R-multiple vs the break-stop.

**Phone push** (existing push infra): LOWER_TOUCH → "SPY 5m: touching your channel bottom. Mid +0.8% /
top +1.6% / stop −0.4%." BREAK → "channel broke". Respect alert-fatigue: one push per event kind per
experiment per session.

## API + UI
- `POST /api/channels/arm {drawing_id}` · `POST /api/channels/{id}/close` · `GET /api/channels`
  (experiments + latest events) · `GET /api/channels/stats`.
- Chart: armed channels render distinct (e.g. solid vs dashed) with a small status chip; touches marked.
- **Scoreboard** — new "Channels (forward)" tab on the Strategy Lab page (`STRATEGY_LAB_SPEC.md`): per-
  experiment event timeline + round outcomes; aggregates once n permits: touch→mid rate, touch→top rate,
  touch→break rate, avg R per round, split by timeframe and slope sign (up/flat/down channels).
  **Honesty banner: "forward paper measurement — small n, no backtest behind these numbers."** Win-rate
  cells show n alongside; no significance claims below n=30 per cell.

## Verify (serv)
- Arm a channel on SPY 5m → row frozen; edit the drawing → experiment geometry unchanged.
- Replay a synthetic bar feed through the monitor: touch/mid/top/break each fire once, sim round P&L
  matches hand-math, push lands on the phone.
- Weekend gap: line value Monday == Friday + 1 bar-step (bar-index extension test).
- `pytest` unit: geometry math (slope per bar, tolerance bands), BREAK-before-MID ordering within one bar
  resolves pessimistically (stop wins ties).

## Later (not this round)
- Algo channel detector run against Alpaca 1-min history (2016+) + the /ES archive using the SAME event
  definitions (this file is the scoring contract) — the scale backtest of the pattern itself.
- Blind-replay calibration mode (draw on a truncated chart, score the reveal) — measures the eye offline.
- Drawn-channel features (slope, width, touches) as decision-engine perception inputs once n exists.
