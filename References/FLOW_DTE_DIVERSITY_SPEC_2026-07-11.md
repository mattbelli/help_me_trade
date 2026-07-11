# Spec: DTE-diversity rule for the options-flow active set

*2026-07-11 · founder-requested. Status: SPEC READY — not built.*
*Owner when built: `signals/options_flow/poll.py` (+ constants). No widget changes needed.*

## Problem

The intraday detector only ever sees near-dated contracts, so the live tape is ~97% 0–1 DTE
(Fri 2026-07-10 session: 623/764 tape events 0 DTE, 117 more 1 DTE, premium p50 $42k).
Long-dated whale flow — someone buying $2M of 45-DTE calls, the single most interesting
kind of print — can only be caught the next morning by the EOD scan, as a non-directional
`unusual_volume` row with no aggressor side.

**Root cause** (`poll.py::_refresh_active_set`): the active set = top
`ACTIVE_SET_PER_UNDERLYING` (60) contracts per underlying ranked purely by
most-recent-trade `(ts_ms, size)`. On NVDA/TSLA the most recently traded contracts are
overwhelmingly same-week expiry, so the set self-selects to 0DTE and the detectors
(sweep/block/pressure) never watch anything longer-dated.

## Rule

Fill each underlying's 60 slots by DTE-bucket quota instead of one global recency rank:

| bucket (DTE) | quota | rationale |
|---|---|---|
| 0–1   | 30 | where the volume is; keep the current tape intact |
| 2–7   | 12 | weeklies |
| 8–30  | 10 | monthlies — earnings/positioning flow |
| 31+   | 8  | the long-dated whale lane |

- **Within a bucket**: rank exactly as today — `(ts_ms, size)` desc on today's latest trade.
  A contract still must have traded today to be a candidate (no dead subscriptions).
- **Spill**: if a bucket has fewer candidates than its quota, unused slots spill to the
  next bucket in order `0–1 → 2–7 → 8–30 → 31+ → wrap to 0–1`. Total stays ≤ 60.
- `dte` is already computed in `_meta_from_occ` / `_meta_from_contract`; bucketing needs
  no new data.

## Why this shape

- **Zero extra load**: active set stays ≤60/underlying × ≤25 underlyings — the poll
  chunking (`POLL_QUOTE_CHUNK`), OPRA quote-flood math, and 5-min refresh cadence are
  untouched. This is a *selection* change, not a *capacity* change.
- **Detector thresholds unchanged**: sweeps/blocks on 31+ DTE contracts are rarer but
  each print is bigger (premium ≈ size × price × 100, and long-dated marks are richer),
  so the existing $25k premium floor does the right thing without retuning.
- **Tape composition is honest**: the widget's score already rewards premium size +
  confirmed aggressor; a $2M 45-DTE buy-side sweep will land at the top of the tape on
  its own — no special-casing in the UI.

## Implementation sketch (~30 lines)

1. `constants.py`: add
   `ACTIVE_SET_DTE_QUOTAS: tuple[tuple[int, int, int], ...] = ((0, 1, 30), (2, 7, 12), (8, 30, 10), (31, 10_000, 8))`
   (lo, hi, quota) — keep `ACTIVE_SET_PER_UNDERLYING = 60` as the invariant total.
2. `poll.py::_refresh_active_set`: after building `ranked`, partition by
   `_dte_days(...)` into the quota buckets, take per-bucket heads, spill leftovers in
   the order above, then proceed exactly as today (`new_active[occ] = meta`).
3. Log one line per refresh at DEBUG: bucket composition per underlying
   (`0-1: 30, 2-7: 9(+3 spilled), ...`) so a session's selection is auditable.

## Verify

- **Unit** (`tests/test_options_flow_poll.py`): synthetic candidate list spanning all
  buckets → quotas respected; a bucket short of candidates spills; total ≤ 60; ranking
  within a bucket preserved.
- **Live (next session)**: `options_flow_events` DTE distribution for sweep/block should
  show >1-DTE events by the close; compare against Fri 2026-07-10's 97%-0DTE baseline.
- Existing detector tests must pass unchanged (selection feeds them; their logic is
  untouched).

## Out of scope

- No change to EOD scan (`ZERO_DTE_EXCLUDE` etc.), detector thresholds, widget, or API.
- No per-underlying custom quotas (revisit only if index products need their own mix).
