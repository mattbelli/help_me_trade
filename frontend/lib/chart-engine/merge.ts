/**
 * Live-tick merge — the contract that made the TV 1D→4H page-kill impossible
 * here by construction (CHART_ENGINE.md → Live data contract): a live price
 * may mutate ONLY the last bar. It never appends, reorders, or touches
 * history. Bar rollover is a separate, explicit act (`rolledOverBar`), never a
 * side effect of a price tick.
 */

import type { EngineBar } from "./types";

/**
 * Merge a live price into the last bar. Returns true when the bar changed
 * (callers repaint only then). Non-finite/non-positive prices and empty data
 * are no-ops.
 */
export function applyLive(bars: EngineBar[], price: number): boolean {
  if (bars.length === 0) return false;
  if (!Number.isFinite(price) || price <= 0) return false;
  const last = bars[bars.length - 1];
  if (price === last.close && price <= last.high && price >= last.low) {
    return false;
  }
  last.close = price;
  if (price > last.high) last.high = price;
  if (price < last.low) last.low = price;
  return true;
}

/**
 * Reconcile a freshly fetched series with the locally-formed tail.
 *
 * A periodic refresh exists to adopt vendor corrections — it must never REGRESS
 * the tail. Dropping a bar we rolled over locally makes the next tick roll over
 * again and re-open it at the CURRENT price, painting a gap the size of
 * everything that moved since the payload was built (up to a full cache TTL).
 * Vendor bars always win; ours survive only past the end of what it published,
 * so this can neither duplicate a timestamp nor reorder the series.
 */
export function mergeFetchedBars(
  fetched: EngineBar[],
  local: readonly EngineBar[],
): EngineBar[] {
  if (fetched.length === 0) return fetched; // an empty payload clears, as before
  const lastFetched = fetched[fetched.length - 1].time;
  const tail = local.filter((b) => b.time > lastFetched);
  return tail.length ? [...fetched, ...tail] : fetched;
}

/**
 * A fresh forming bar `steps` buckets after `prev`, spanning its open to
 * `price`. Pure construction — the caller decides whether appending is
 * legitimate. `steps > 1` skips buckets rather than fabricating them: one bar
 * lands in the bucket containing `now` and the missing ones stay missing until
 * a refetch supplies them.
 *
 * The open depends on contiguity. A contiguous rollover (`steps === 1`) opens
 * at `prev.close` — see below. A skipped-bucket rollover opens at `price`,
 * because bars are genuinely absent between the two and the gap IS the honest
 * rendering: on a thin symbol (FXE, measured 2026-08-12: 69.5% of RTH minute
 * bars are >=2 intervals apart, median gap 180s, max 40min) nothing traded in
 * between, so the first print of the new bucket is its open.
 *
 * The contiguous case opens at the PREVIOUS bar's close, not at `price`.
 * Opening at `price`
 * fabricated a visible gap on every rollover, because the bar we follow closed
 * at a VENDOR SNAPSHOT that trails real time: measured 2026-08-12 on TSLA 1Min,
 * the served tail sat 93-133s behind with its close frozen at 325.91 while the
 * live tape was 326.04 — and the drift grows with staleness. Worse, that window
 * is exactly when no live tick had a chance to walk the previous close forward:
 * `native-chart`'s staleness gate blocks the merge while the tail is >=2
 * intervals old, so the first tick after a fresh payload lands rolls over
 * immediately and the whole vendor lag becomes one jump. Distinct from the
 * 07-29 phantom-gap fix (a cache HIT re-applying a payload and deleting the
 * forming bar) — that one is fixed and still stands; this is the cache-MISS
 * path, where the vendor bar legitimately replaces ours but is already stale.
 *
 * Opening at the prior close draws the move as a wick instead of a gap, which
 * is the honest shape: we know price travelled between those two points, we
 * just don't have the vendor's bucket boundary yet. Vendor truth overwrites the
 * whole bar within a refresh or two.
 */
export function rolledOverBar(
  prev: EngineBar,
  intervalMs: number,
  price: number,
  steps = 1,
): EngineBar {
  const open = steps > 1 ? price : prev.close;
  return {
    time: prev.time + intervalMs * steps,
    open,
    high: Math.max(open, price),
    low: Math.min(open, price),
    close: price,
    volume: 0,
  };
}
