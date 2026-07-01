// Simple moving average. out[i] = mean(values[i-n+1..i]); null until index n-1.
export function smaSeries(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    out.push(i >= n - 1 ? sum / n : null);
  }
  return out;
}

// EMA seeded with the SMA of the first n values (matches indicators.py::ema).
export function emaSeries(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < n) return out;
  const k = 2 / (n + 1);
  let ema = 0;
  for (let i = 0; i < n; i++) ema += values[i];
  ema /= n;
  out[n - 1] = ema;
  for (let i = n; i < values.length; i++) {
    ema = (values[i] - ema) * k + ema;
    out[i] = ema;
  }
  return out;
}

// RSI with Wilder smoothing (matches indicators.py::rsi). Defined from index n.
export function rsiSeries(closes: number[], n = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length <= n) return out;
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= n; i++) {
    const ch = closes[i] - closes[i - 1];
    avgGain += Math.max(ch, 0);
    avgLoss += Math.max(-ch, 0);
  }
  avgGain /= n;
  avgLoss /= n;
  out[n] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = n + 1; i < closes.length; i++) {
    const ch = closes[i] - closes[i - 1];
    avgGain = (avgGain * (n - 1) + Math.max(ch, 0)) / n;
    avgLoss = (avgLoss * (n - 1) + Math.max(-ch, 0)) / n;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

// MACD: three aligned series. macd = EMA(fast) - EMA(slow); signal = EMA(signalPeriod) of the macd line;
// histogram = macd - signal. Reuses emaSeries (which seeds with an SMA of the first n).
export function macdSeries(
  closes: number[],
  fast = 12,
  slow = 26,
  signalPeriod = 9,
): { macd: (number | null)[]; signal: (number | null)[]; histogram: (number | null)[] } {
  const emaFast = emaSeries(closes, fast);
  const emaSlow = emaSeries(closes, slow);
  const macd: (number | null)[] = closes.map((_, i) =>
    emaFast[i] != null && emaSlow[i] != null ? (emaFast[i] as number) - (emaSlow[i] as number) : null,
  );
  const signal: (number | null)[] = new Array(closes.length).fill(null);
  const histogram: (number | null)[] = new Array(closes.length).fill(null);
  const firstIdx = macd.findIndex((v) => v != null);
  if (firstIdx >= 0) {
    const macdVals = macd.slice(firstIdx) as number[];
    const sig = emaSeries(macdVals, signalPeriod);
    for (let j = 0; j < sig.length; j++) {
      const i = firstIdx + j;
      signal[i] = sig[j];
      if (macd[i] != null && sig[j] != null) histogram[i] = (macd[i] as number) - (sig[j] as number);
    }
  }
  return { macd, signal, histogram };
}
