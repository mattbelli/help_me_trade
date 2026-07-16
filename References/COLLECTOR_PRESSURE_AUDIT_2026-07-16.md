# Collector + Flow-Pressure Architecture Audit — 2026-07-16

**Auditor:** Fable-5 agent (adversarial, read-only), founder-mandated ("absolutely bulletproof").
**Subject:** PR #93 branch (`feat/index-pressure-gauge`) + both collector processes.
**Verdict:** measurement core honest and correct; the assumed DATA SOURCE (wildcard OPRA
trades lane) does not exist anywhere in the codebase — the gauge as first written would
have calibrated three weeks of baseline on a silently-truncated slice. Fixed same day.

## Findings + remediation status (fixes landed on the PR the same evening)

| # | Sev | Finding | Status |
|---|-----|---------|--------|
| F1 | CRIT | No wildcard trades lane exists (`"*"` never subscribed by any production code — the "rotates to wildcard" belief traces to research probes). Capped lane excludes index 0-1DTE (~57-60% of SPY/QQQ flow = the bulk of opening index flow); 7/13 gauge names not on any watchlist -> zero rows forever, undisclosed. | **FIXED**: guaranteed gauge lane — `GAUGE_FETCH_UNDERLYINGS` always fetched (outside the watchlist cap), index 0-1DTE picked into a reserved `GAUGE_LANE_BUDGET` allocated BEFORE the regular set; `gauge_lane` meta flag; `rolling.py` pins detectors' old behavior (they never see index 0DTE). SPX fail-soft. |
| F2 | HIGH | Flush-time coverage stamp retroactively rewrote every minute's provenance; based on SENT not ACKED subs. | **FIXED**: coverage stamped per minute-cell at ACCRUAL time (first mode sticks, mid-minute change -> `mixed`), from `stream.acked_market_wide`; upsert never relabels a stored coverage (different -> `mixed`). |
| F3 | HIGH | `direction=buy_side` means aggressor-side in burst/EOD-pressure but market-direction in the gauge — downstream renderers color a put-buying wave bullish. Pre-existing on main. | **DEFERRED** (follow-up): stamp `detail.direction_semantics` + fix renderers. |
| F4 | MED | Collector interlock is a startup-latched env var, not mutual exclusion — two writers can oscillate rows silently. | **DEFERRED** (follow-up): single-writer lease row. |
| F5 | MED | Restart-truncation indistinguishable from quiet/absent data. | **FIXED**: `updated_at` on minute rows + `index_pressure_days` completeness sidecar (minutes_observed, syms, coverage_modes, writer pid + `writers` restart marker, finalized_at). |
| F6 | MED | Half-days (13:00 close) contaminate the close series. | **FIXED**: `market_calendar.is_early_close()` (Jul 3 / day-after-Thanksgiving / Dec 24 rules, observed-holiday aware); close window never recorded on half days, day row = `session_type=half`. Session TIMES still out of scope (board: market-hours-half-day-early-close). |
| F7 | MED | `collected_prints` deque overflow silently drops oldest; gauge had no completeness log. | **FIXED**: `dropped_prints` counter + warning + stats-line field; per-window finalize log line. |
| F8 | MED | `directional_pressure` is a trailing-20k-print net labeled "today"; DIR-1 at poll-time vs at-print undisclosed. Pre-existing on main. | **DEFERRED** (follow-up): caveat additions. |
| F9 | LOW | DIR-1 degradation paths honest (lands in unclassified, surfaced); quote-anchor ranking note for gauge contracts. | Accepted; gauge 0DTE picks carry high day-$vol -> natural anchor priority. |
| F10 | LOW | Day-rollover/window-edge/DST verified correct; two nits (post-16:05 stragglers unflushed; burst re-arm resets on restart — pre-existing). | Accepted. |
| F11 | NIT | Dust floor duplicated; close-window caveat said "opening"; widget comment stale. | **FIXED** (all three). |

## Design decisions ratified by the audit
- Running-total upsert (bounded loss, never double-count) over append-only log — kept; deficiency was provenance, now fixed (F5).
- In-memory window over DB read-modify-write — kept.
- Gauge data source: guaranteed subscription lane on the ONE stream (no dual-lane dedup problem); a REST pull was the audit's alternative — rejected in favor of the lane to avoid cross-lane double-counting.
- `WINDOWS` parameterized to (start, length) — future windows (OpEx pin, midday) need not be 30 min.

## Study-layer verdict (rolling/calendar studies)
- Key `(underlying, date, window, minute)` supports every named study (close(t) vs open(t+1) = next-trading-day self-join; weekday/month-position/OpEx cuts are pure functions of `date`). No calendar columns needed; daily rollup = GROUP BY.
- Sample gates (n>=20/cell): pooled baselines ~4 weeks; per-weekday ~20 weeks; OpEx/payday-Friday cells 1.5-2+ years. Pre-register the cuts now; label everything below-gate as exploratory. NOTHING gauge-based is backfillable; volume seasonality IS answerable from deep equity daily-bar history (PC/Norgate for depth — serv slice is survivor-biased, never publish from it).

## Verified clean (live evidence)
Flow subs acked 9 min pre-open at today's bell; equity collector 20k msg/s, lag p50 16ms; zero SQLite lock errors across three writer processes since 07-14; day-rollover cannot clip the close window's final flush; DST-safe (ET wall-clock bucketing).
