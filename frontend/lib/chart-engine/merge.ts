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
 * A fresh forming bar for the next interval bucket, opened at `price`.
 * Pure construction — the caller decides whether appending is legitimate
 * (single-step rollover only; gaps are a refetch, never fabricated buckets).
 */
export function rolledOverBar(lastBarTimeMs: number, intervalMs: number, price: number): EngineBar {
  return {
    time: lastBarTimeMs + intervalMs,
    open: price,
    high: price,
    low: price,
    close: price,
    volume: 0,
  };
}
