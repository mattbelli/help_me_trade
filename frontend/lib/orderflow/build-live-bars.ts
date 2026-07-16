import type {
  CandleBar,
  FootprintBar,
  FootprintData,
  TradeEvent,
} from "@/lib/orderflow-api";

const TICK = 0.25;

function computeBarMs(footprint: FootprintData): number {
  return footprint.bars.length > 1
    ? footprint.bars[1].t - footprint.bars[0].t
    : 30_000;
}

/** Epoch-ms bar start aligned to barMs boundaries (matches pandas floor). */
function floorBarMs(t: number, barMs: number): number {
  return Math.floor(t / barMs) * barMs;
}

function tradeSlice(
  trades: TradeEvent[],
  fromMs: number,
  toMs: number,
): TradeEvent[] {
  if (trades.length === 0) return [];

  let lo = 0;
  let hi = trades.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (trades[mid].t < fromMs) lo = mid + 1;
    else hi = mid;
  }
  const start = lo;

  lo = start;
  hi = trades.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (trades[mid].t <= toMs) lo = mid + 1;
    else hi = mid;
  }
  return trades.slice(start, lo);
}

function buildLiveFootprintBar(
  barT: number,
  barTrades: TradeEvent[],
  priorCumDelta: number,
): FootprintBar {
  const cellMap = new Map<number, { bidVol: number; askVol: number }>();

  for (const tr of barTrades) {
    const priceTick = Math.round(tr.price / TICK) * TICK;
    const cell = cellMap.get(priceTick) ?? { bidVol: 0, askVol: 0 };
    if (tr.side === "B") cell.askVol += tr.size;
    else if (tr.side === "A") cell.bidVol += tr.size;
    cellMap.set(priceTick, cell);
  }

  const cells = Array.from(cellMap.entries())
    .filter(([, v]) => v.bidVol > 0 || v.askVol > 0)
    .map(([price, v]) => ({
      price,
      bidVol: v.bidVol,
      askVol: v.askVol,
    }))
    .sort((a, b) => a.price - b.price);

  const delta = cells.reduce((sum, c) => sum + (c.askVol - c.bidVol), 0);

  return {
    t: barT,
    cells,
    delta,
    cumDelta: priorCumDelta + delta,
  };
}

function buildLiveCandleBar(barT: number, barTrades: TradeEvent[]): CandleBar | null {
  if (barTrades.length === 0) return null;

  let o = barTrades[0].price;
  let h = barTrades[0].price;
  let l = barTrades[0].price;
  let c = barTrades[0].price;

  for (let i = 1; i < barTrades.length; i++) {
    const p = barTrades[i].price;
    if (p > h) h = p;
    if (p < l) l = p;
    c = p;
  }

  return { t: barT, o, h, l, c };
}

export type LiveBarsResult = {
  footprint: FootprintData;
  candles: CandleBar[];
  barMs: number;
};

/**
 * Merge pre-aggregated completed bars with a live-built current bar from raw trades.
 * When cursorMs is null, returns the original pre-aggregated data unchanged.
 */
export function buildLiveBars(
  footprint: FootprintData,
  candles: CandleBar[],
  trades: TradeEvent[],
  cursorMs: number | null,
): LiveBarsResult {
  const barMs = computeBarMs(footprint);

  if (cursorMs == null) {
    return { footprint, candles, barMs };
  }

  const barStart = floorBarMs(cursorMs, barMs);
  const barEnd = barStart + barMs;

  const completedFp = footprint.bars.filter((b) => b.t + barMs <= cursorMs);
  const completedCandles = candles.filter((b) => b.t + barMs <= cursorMs);

  const priorCumDelta =
    completedFp.length > 0 ? completedFp[completedFp.length - 1].cumDelta : 0;

  const barTrades = tradeSlice(trades, barStart, cursorMs);
  const liveFpBar = buildLiveFootprintBar(barStart, barTrades, priorCumDelta);
  const liveCandle = buildLiveCandleBar(barStart, barTrades);

  const mergedFootprint: FootprintData = {
    bars: [...completedFp, liveFpBar],
  };

  const mergedCandles = liveCandle
    ? [...completedCandles, liveCandle]
    : completedCandles;

  return {
    footprint: mergedFootprint,
    candles: mergedCandles,
    barMs,
  };
}
