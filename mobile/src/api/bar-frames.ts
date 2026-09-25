/**
 * Server bar frames, fanned out per symbol. serv is the candle authority: every
 * quote frame carries the forming 1-minute bar (full OHLC so far), and sealed
 * minutes / vendor corrections arrive as `bar` events. Charts fold these in
 * (features/chart/live-bars.ts) instead of painting a bare price onto a stale
 * candle.
 */

export interface BarFrame {
  symbol: string;
  timeframe: string; // '1Min' | '1Day'
  time: number; // bucket start, epoch ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  session?: string; // 'pre' | 'regular' | 'post' | 'overnight'
}

type Listener = (frame: BarFrame) => void;

const listeners = new Map<string, Set<Listener>>();

export function parseBarFrame(raw: unknown, symbol?: string): BarFrame | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const sym = typeof r.symbol === 'string' ? r.symbol : symbol;
  const nums = [r.time, r.open, r.high, r.low, r.close];
  if (!sym || typeof r.timeframe !== 'string' || !nums.every((n) => typeof n === 'number')) {
    return null;
  }
  return {
    symbol: sym,
    timeframe: r.timeframe,
    time: r.time as number,
    open: r.open as number,
    high: r.high as number,
    low: r.low as number,
    close: r.close as number,
    volume: typeof r.volume === 'number' ? r.volume : 0,
    session: typeof r.session === 'string' ? r.session : undefined,
  };
}

export function publishBarFrame(frame: BarFrame): void {
  listeners.get(frame.symbol)?.forEach((l) => l(frame));
}

export function subscribeBarFrames(symbol: string, listener: Listener): () => void {
  let set = listeners.get(symbol);
  if (!set) {
    set = new Set();
    listeners.set(symbol, set);
  }
  set.add(listener);
  return () => {
    set.delete(listener);
    if (set.size === 0) listeners.delete(symbol);
  };
}
