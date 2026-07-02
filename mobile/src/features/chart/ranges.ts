/** Chart range → serv bars query (timeframe + lookback days). Shared by the
 *  inline preview (a subset) and the full-screen chart (all of them). */
export const CHART_RANGES = [
  { key: '1D', timeframe: '5Min', days: 1 },
  { key: '1W', timeframe: '1Hour', days: 7 },
  { key: '1M', timeframe: '1Day', days: 31 },
  { key: '3M', timeframe: '1Day', days: 95 },
  { key: '1Y', timeframe: '1Day', days: 366 },
  { key: '5Y', timeframe: '1Week', days: 1830 },
] as const;

export type RangeKey = (typeof CHART_RANGES)[number]['key'];

/** Ranges shown on the compact inline chart. */
export const INLINE_RANGE_KEYS: RangeKey[] = ['1D', '1W', '1M', '1Y'];

export function rangeFor(key: RangeKey) {
  return CHART_RANGES.find((r) => r.key === key) ?? CHART_RANGES[2];
}

// Full-screen chart: explicit candle INTERVAL (the candle size) + how much
// history to load for it. Pannable/zoomable beyond what's loaded.
export const INTERVALS = [
  { key: '1m', timeframe: '1Min', days: 2 },
  { key: '5m', timeframe: '5Min', days: 5 },
  { key: '15m', timeframe: '15Min', days: 10 },
  { key: '30m', timeframe: '30Min', days: 20 },
  { key: '1h', timeframe: '1Hour', days: 30 },
  { key: '4h', timeframe: '4Hour', days: 90 },
  { key: '1D', timeframe: '1Day', days: 365 },
  { key: '1W', timeframe: '1Week', days: 1825 },
] as const;

export type IntervalKey = (typeof INTERVALS)[number]['key'];

export function intervalFor(key: IntervalKey) {
  return INTERVALS.find((i) => i.key === key) ?? INTERVALS[6]; // default 1D
}

/** Sensible candle interval to open the full-screen chart at, given the inline range. */
export const RANGE_TO_INTERVAL: Record<RangeKey, IntervalKey> = {
  '1D': '5m',
  '1W': '1h',
  '1M': '1D',
  '3M': '1D',
  '1Y': '1D',
  '5Y': '1W',
};
