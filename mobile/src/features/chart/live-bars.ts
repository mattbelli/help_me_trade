/**
 * Live candles: fold serv's bar frames (api/bar-frames.ts) into a fetched
 * series. Mirrors the web chart's merge (frontend/lib/chart-engine/merge.ts):
 * the server builds the bars, the client only replaces/widens/appends by bar
 * time. The chart used to paint the header price onto the last FETCHED candle
 * with no memory — a wick vanished as soon as price came back, and a new
 * minute kept redrawing the old candle until the next fetch (2026-09-25).
 */

import { useEffect, useRef, useState } from 'react';

import { subscribeBarFrames, type BarFrame } from '@/api/bar-frames';
import type { Bar } from '@/api/types';

const MINUTE_MS = 60_000;
const DAY_MS = 86_400_000;

export const TIMEFRAME_MS: Record<string, number> = {
  '1Min': MINUTE_MS,
  '5Min': 5 * MINUTE_MS,
  '15Min': 15 * MINUTE_MS,
  '30Min': 30 * MINUTE_MS,
  '1Hour': 60 * MINUTE_MS,
  '4Hour': 240 * MINUTE_MS,
  '1Day': DAY_MS,
  '1Week': 7 * DAY_MS,
};

function toBar(frame: BarFrame, time: number): Bar {
  return {
    time: new Date(time).toISOString(),
    open: frame.open,
    high: frame.high,
    low: frame.low,
    close: frame.close,
    volume: frame.volume,
  };
}

/**
 * Fold one frame into `bars` in place (new objects, never mutating a fetched
 * bar). Intraday charts are extended-hours (the /api/bars default); daily+
 * charts take regular-session minutes only. Returns false when ignored.
 */
export function applyBarFrame(bars: Bar[], frame: BarFrame, intervalMs: number): boolean {
  if (bars.length === 0 || !(intervalMs > 0)) return false;
  if (!Number.isFinite(frame.close) || frame.close <= 0) return false;
  const intraday = intervalMs < DAY_MS;
  const isMinute = frame.timeframe === '1Min';
  if (intraday && !isMinute) return false;
  if (!intraday && isMinute && frame.session !== 'regular') return false;
  const exact = intervalMs === (isMinute ? MINUTE_MS : DAY_MS);
  const lastIndex = bars.length - 1;
  const last = bars[lastIndex];
  const lastTime = Date.parse(last.time);

  let bucket: number;
  if (exact) {
    bucket = frame.time;
  } else if (isMinute && !intraday) {
    // A minute only widens the daily+ bar the vendor already opened — the
    // vendor owns where those buckets start; the next one comes with a fetch.
    if (frame.time < lastTime || frame.time >= lastTime + intervalMs) return false;
    bucket = lastTime;
  } else if (isMinute) {
    bucket = frame.time - (frame.time % intervalMs);
  } else if (frame.time < lastTime) {
    return false;
  } else {
    bucket = frame.time - lastTime < intervalMs ? lastTime : frame.time;
  }

  if (bucket < lastTime) {
    if (!exact) return false;
    // Correction to a kept bar (a sealed minute / vendor update).
    for (let i = lastIndex - 1; i >= 0; i--) {
      const t = Date.parse(bars[i].time);
      if (t < bucket) return false;
      if (t === bucket) {
        bars[i] = toBar(frame, bucket);
        return true;
      }
    }
    return false;
  }

  if (bucket === lastTime) {
    // Widen, never narrow: Schwab's futures stream skips ticks, so serv's
    // forming bar can miss a wick the fetched bar already has (/NQ measured
    // 0.25-4.5 pt short, 2026-09-25).
    const base = exact ? toBar(frame, bucket) : { ...last, close: frame.close };
    bars[lastIndex] = {
      ...base,
      high: Math.max(last.high, frame.high),
      low: Math.min(last.low, frame.low),
    };
    return true;
  }

  bars.push(toBar(frame, bucket));
  return true;
}

const EMPTY: Bar[] = [];

/** The fetched series with serv's live bar frames folded in, repainted at most once per animation frame. */
export function useLiveBars(symbol: string, fetched: Bar[] | undefined, timeframe: string): Bar[] {
  const base = fetched ?? EMPTY;
  const [bars, setBars] = useState<Bar[]>(base);
  const ref = useRef<Bar[]>(base);

  useEffect(() => {
    ref.current = base.slice();
    setBars(ref.current);
  }, [base]);

  useEffect(() => {
    const intervalMs = TIMEFRAME_MS[timeframe];
    if (!symbol || !intervalMs) return;
    let raf: number | null = null;
    const unsubscribe = subscribeBarFrames(symbol, (frame) => {
      if (!applyBarFrame(ref.current, frame, intervalMs)) return;
      raf ??= requestAnimationFrame(() => {
        raf = null;
        setBars(ref.current.slice());
      });
    });
    return () => {
      unsubscribe();
      if (raf != null) cancelAnimationFrame(raf);
    };
  }, [symbol, timeframe]);

  return bars;
}
