// Symbol-screen chart: explicit candle INTERVAL (the candle size) + how much
// history to load for it. Pannable/zoomable beyond the default view.
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
