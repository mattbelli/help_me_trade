/**
 * Live data contract (CHART_ENGINE.md → Live data contract, rewritten for
 * card #325): the chart never invents candles. Bars arrive from ONE authority
 * — the backend's minute-bar builder (SIP prints bucketed by their own
 * timestamps, Alpaca `bars`/`updatedBars` replacing ours minute-for-minute)
 * — as `bar` frames, and the client's whole job is TradingView's rule:
 * same time → replace, newer → append, older → leave history alone.
 *
 * Nothing here consults the wall clock, a bare price, a staleness gate or a
 * plausibility band. Those lived in the browser from 07-11 to 08-17 and each
 * one was a bandaid over the vendor tail trailing real time.
 */

import type { EngineBar } from "./types";

/** A `bar` frame (or the `bar` field on a quote frame) from /ws/market. */
export type BarFrame = {
  symbol: string;
  timeframe: "1Min" | "1Day";
  /** Epoch ms, bucket start (minute, or the vendor's 04:00Z daily stamp). */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  /** True once the vendor's own bar for this bucket replaced the built one. */
  final: boolean;
  session: "pre" | "regular" | "post" | "overnight";
};

export type ApplyResult =
  | { kind: "ignored" }
  | { kind: "replaced"; index: number }
  | { kind: "appended"; index: number; skipped: number };

/**
 * Caller-owned memory for coarse-bucket volume roll-up. `base` is the bar's
 * volume when frames first touched this bucket (the vendor's figure, which may
 * already include the minute/day in progress at that moment); per frame time
 * we keep the first and latest volume seen. Reset it to null whenever a fetched
 * payload replaces the bars — the new base then includes everything summed so
 * far, and summing on top would double count.
 */
export type VolumeMemory = {
  bucket: number;
  base: number;
  seen: Map<number, { first: number; latest: number }>;
};
export type VolumeMemoryRef = { current: VolumeMemory | null };

const MINUTE_MS = 60_000;

/**
 * Reconcile a freshly fetched WINDOW (the newest N bars, or a periodic
 * refresh of them) with the local series. The vendor's bars replace ours over
 * the span they cover; ours survive on both sides — history the browser
 * backfilled before the window, and the hub-built tail past it. Same-provider
 * ascending times on both sides, so this can neither duplicate a timestamp nor
 * reorder the series. An empty refresh keeps what we have (an upstream hiccup
 * must not wipe a chart); a symbol/timeframe switch never merges at all.
 */
export function mergeFetchedBars(
  fetched: EngineBar[],
  local: readonly EngineBar[],
): EngineBar[] {
  if (fetched.length === 0) return local.length ? [...local] : fetched;
  const first = fetched[0].time;
  const last = fetched[fetched.length - 1].time;
  const older = local.filter((b) => b.time < first);
  const newer = local.filter((b) => b.time > last);
  if (older.length === 0 && newer.length === 0) return fetched;
  return [...older, ...fetched, ...newer];
}

/**
 * Prepend an older page (bars strictly before our oldest) to the series.
 * Returns the new array and how many bars were added — the engine shifts the
 * view by that many so what's on screen stays put.
 */
export function prependFetchedBars(
  page: EngineBar[],
  local: readonly EngineBar[],
): { bars: EngineBar[]; added: number } {
  if (local.length === 0) return { bars: page, added: page.length };
  const cutoff = local[0].time;
  const older = page.filter((b) => b.time < cutoff);
  if (older.length === 0) return { bars: [...local], added: 0 };
  return { bars: [...older, ...local], added: older.length };
}

/**
 * Fold one bar frame into the series in place. Returns what happened so the
 * caller can repaint (and, on a skipped bucket, kick a refetch for the hole).
 *
 * - `frame.timeframe` must match the series' family: 1Min frames drive every
 *   intraday chart (rolled up into the chart's bucket by UTC floor — the
 *   vendor's own alignment); 1Day frames drive 1Day/1Week/1Month.
 * - Inside the last bucket: exact-interval charts (1Min ← 1Min, 1Day ← 1Day)
 *   REPLACE the bar — the frame is the authority. Coarser charts widen
 *   high/low, take the close, and grow volume by what the frames add on top
 *   of the vendor's figure (`memory`; see `rollupVolume`).
 * - Past the last bucket: append ONE bar in the bucket containing the frame,
 *   opening at the frame's open — a real print, never a guessed continuation.
 *   Buckets in between stay absent (`skipped > 0`) for the refetch to fill.
 * - Before the last bucket: replace an exact-time match on exact-interval
 *   charts (an `updatedBars` correction); otherwise ignore — history is the
 *   vendor's.
 */
export function applyBarFrame(
  bars: EngineBar[],
  frame: BarFrame,
  opts: {
    intraday: boolean;
    intervalMs: number;
    showExt: boolean;
    memory: VolumeMemoryRef;
  },
): ApplyResult {
  if (bars.length === 0) return { kind: "ignored" };
  if (!Number.isFinite(frame.close) || frame.close <= 0) {
    return { kind: "ignored" };
  }
  const isMinute = frame.timeframe === "1Min";
  if (opts.intraday !== isMinute) return { kind: "ignored" };
  if (isMinute && !opts.showExt && frame.session !== "regular") {
    return { kind: "ignored" };
  }
  const interval = opts.intervalMs;
  if (!(interval > 0)) return { kind: "ignored" };
  const frameInterval = isMinute ? MINUTE_MS : 86_400_000;
  const exact = interval === frameInterval;
  const last = bars[bars.length - 1];
  const lastIndex = bars.length - 1;

  // The chart bucket this frame belongs to. Exact charts keep the frame's own
  // stamp (daily bars carry the vendor's 04:00Z-style time, not a UTC floor);
  // coarser intraday charts floor to the interval (vendor alignment); weekly /
  // monthly step from the last bar since their buckets are calendar-shaped.
  let bucket: number;
  if (exact) {
    bucket = frame.time;
  } else if (isMinute) {
    bucket = frame.time - (frame.time % interval);
  } else if (frame.time < last.time) {
    return { kind: "ignored" };
  } else {
    const steps = Math.floor((frame.time - last.time) / interval);
    bucket = steps === 0 ? last.time : frame.time;
  }

  if (bucket < last.time) {
    if (exact) {
      // Correction to a kept bar (Alpaca updatedBars): exact-time replace.
      for (let i = lastIndex - 1; i >= 0 && bars[i].time >= frame.time; i--) {
        if (bars[i].time === frame.time) {
          bars[i] = toBar(frame);
          return { kind: "replaced", index: i };
        }
      }
    }
    return { kind: "ignored" };
  }

  if (bucket === last.time) {
    if (exact) {
      bars[lastIndex] = toBar(frame);
      return { kind: "replaced", index: lastIndex };
    }
    widen(last, frame);
    last.volume = rollupVolume(opts.memory, bucket, frame, last.volume);
    return { kind: "replaced", index: lastIndex };
  }

  // Past the tail: one bar in the bucket containing the frame.
  const skipped =
    exact || isMinute ? Math.round((bucket - last.time) / interval) - 1 : 0;
  const fresh = toBar(frame);
  fresh.time = bucket;
  if (!exact) {
    opts.memory.current = null; // memory belongs to the forming bucket only
    fresh.volume = rollupVolume(opts.memory, bucket, frame, 0);
  }
  bars.push(fresh);
  return { kind: "appended", index: lastIndex + 1, skipped: Math.max(0, skipped) };
}

function toBar(f: BarFrame): EngineBar {
  return {
    time: f.time,
    open: f.open,
    high: f.high,
    low: f.low,
    close: f.close,
    volume: f.volume,
  };
}

function widen(bar: EngineBar, f: BarFrame): void {
  if (f.high > bar.high) bar.high = f.high;
  if (f.low < bar.low) bar.low = f.low;
  bar.close = f.close;
}

/**
 * Volume of a coarse forming bucket = base + Σ over frame times of
 * (latest − first-seen), except that frame times which began AFTER we started
 * watching count in full. `base` is the bar's volume at first touch — the
 * vendor's figure, which can already include the minute/day in progress at
 * that moment, so that one contributes only its growth since; later minutes
 * cannot be in the base and count whole. Exact to within one frame; the
 * caller resets the memory when a fetched payload replaces the bars.
 */
function rollupVolume(
  memory: VolumeMemoryRef,
  bucket: number,
  f: BarFrame,
  current: number,
): number {
  let m = memory.current;
  if (!m || m.bucket !== bucket) {
    m = { bucket, base: current, seen: new Map() };
    memory.current = m;
  }
  const rec = m.seen.get(f.time);
  if (rec) rec.latest = f.volume;
  else m.seen.set(f.time, { first: f.volume, latest: f.volume });
  let firstTime = Infinity;
  for (const t of m.seen.keys()) if (t < firstTime) firstTime = t;
  let sum = m.base;
  for (const [t, r] of m.seen) {
    // Only a non-zero base can already contain the first-seen frame's start.
    sum += t === firstTime && m.base > 0 ? r.latest - r.first : r.latest;
  }
  return sum;
}
