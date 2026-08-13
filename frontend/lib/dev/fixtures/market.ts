/** Market-data fixtures: watchlists, quotes, bars, chain, flow, pressure, scans. */
import type {
  BarsResponse,
  IndexPressureResult,
  IndexPressureTrendResult,
  OptionsChainResult,
  OptionsChainRow,
  OptionsFlowEvent,
  OptionsFlowResult,
  OvernightFlowResult,
  RvolScannerResult,
  SessionLevelsResponse,
  WatchlistAggregate,
  WatchlistQuote,
  WatchlistsState,
} from "@/lib/api";
import type { ScanPreset, ScanRunResult } from "@/lib/scans-api";

import { DAY, NOW_ISO, daysBefore, et, lcg, round2, series } from "./clock";

export const WATCHLIST_SYMBOLS = [
  "SPY",
  "QQQ",
  "AAPL",
  "NVDA",
  "BRK.B",
  "ZVZZTW",
  "MSFT",
  "AMD",
  "TSLA",
  "PLTR",
  "GOOGL",
  "COIN",
  "SOFI",
  "MARA",
  "SMCI",
  "AMZN",
];

export const WATCHLISTS: WatchlistsState = {
  active_id: "core",
  lists: [
    { id: "core", name: "core", symbols: WATCHLIST_SYMBOLS },
    {
      id: "flow",
      name: "flow",
      symbols: ["SPY", "QQQ", "NVDA", "TSLA", "AMD"],
      max_slots: 30,
      pinned: ["SPY", "QQQ"],
    },
    { id: "earnings", name: "earnings", symbols: ["AAPL", "MSFT", "GOOGL"] },
  ],
};

export const WATCHLISTS_EMPTY: WatchlistsState = {
  active_id: "core",
  lists: [{ id: "core", name: "core", symbols: [] }],
};

type QuoteSeed = {
  symbol: string;
  price: number;
  changePct: number;
  volume: number;
  pe: number | null;
  cap: number | null;
};

const QUOTE_SEEDS: QuoteSeed[] = [
  { symbol: "SPY", price: 624.02, changePct: 0.41, volume: 62_118_400, pe: null, cap: null },
  { symbol: "QQQ", price: 561.18, changePct: 0.77, volume: 38_902_100, pe: null, cap: null },
  { symbol: "AAPL", price: 228.71, changePct: 1.22, volume: 51_338_900, pe: 34.1, cap: 3_412_000 },
  { symbol: "NVDA", price: 171.4, changePct: -2.86, volume: 188_442_000, pe: 48.7, cap: 4_180_000 },
  { symbol: "BRK.B", price: 517.86, changePct: 0.18, volume: 3_112_400, pe: 12.4, cap: 1_118_000 },
  { symbol: "ZVZZTW", price: 2.87, changePct: -11.42, volume: 902_100, pe: null, cap: 41 },
  { symbol: "MSFT", price: 489.22, changePct: 0.64, volume: 19_004_800, pe: 37.2, cap: 3_640_000 },
  { symbol: "AMD", price: 189.77, changePct: 3.94, volume: 74_221_300, pe: 41.9, cap: 307_000 },
  { symbol: "TSLA", price: 318.05, changePct: -4.12, volume: 121_884_000, pe: 71.3, cap: 1_020_000 },
  { symbol: "PLTR", price: 38.14, changePct: -1.88, volume: 66_712_500, pe: 88.4, cap: 88_400 },
  { symbol: "GOOGL", price: 206.4, changePct: 0.92, volume: 22_118_700, pe: 25.8, cap: 2_510_000 },
  { symbol: "COIN", price: 271.33, changePct: -5.71, volume: 14_882_200, pe: 33.1, cap: 68_900 },
  { symbol: "SOFI", price: 15.91, changePct: 2.14, volume: 41_002_900, pe: 22.5, cap: 18_200 },
  { symbol: "MARA", price: 16.42, changePct: -6.33, volume: 55_119_800, pe: null, cap: 5_900 },
  { symbol: "SMCI", price: 51.28, changePct: 7.02, volume: 32_884_100, pe: 18.9, cap: 30_100 },
  { symbol: "AMZN", price: 241.77, changePct: 0.33, volume: 30_112_600, pe: 39.6, cap: 2_580_000 },
];

function quote(seed: QuoteSeed, i: number): WatchlistQuote {
  const prev = round2(seed.price / (1 + seed.changePct / 100));
  return {
    price: seed.price,
    change_pct: seed.changePct,
    sparkline: series(prev, 40, 0.004, 1000 + i * 37),
    intraday: series(prev, 78, 0.003, 5000 + i * 41),
    prev_close: prev,
    gap_pct: round2(seed.changePct * 0.3),
    volume: seed.volume,
    day_high: round2(seed.price * 1.011),
    day_low: round2(seed.price * 0.988),
    rvol: { value: round2(0.7 + (i % 7) * 0.31), state: "live" },
    market_session: "regular",
    ext_price: null,
    ext_change_pct: null,
    pe: seed.pe,
    eps: seed.pe ? round2(seed.price / seed.pe) : null,
    div_yield: i % 3 === 0 ? round2(0.4 + (i % 5) * 0.22) : null,
    market_cap: seed.cap,
    last_earnings: daysBefore(30 + (i % 40)),
    next_earnings: daysBefore(-(20 + (i % 30))),
    bid: round2(seed.price - 0.02),
    ask: round2(seed.price + 0.02),
    w52_high: round2(seed.price * 1.34),
    w52_low: round2(seed.price * 0.61),
    atr14: round2(seed.price * 0.021),
    sma50: round2(seed.price * 0.97),
    sma200: round2(seed.price * 0.88),
  };
}

export const WATCHLIST_MARKET = {
  quotes: Object.fromEntries(
    QUOTE_SEEDS.map((s, i) => [s.symbol, quote(s, i)]),
  ) as Record<string, WatchlistQuote>,
  spy_change_pct: 0.41,
};

export const WATCHLIST_MARKET_EMPTY = { quotes: {}, spy_change_pct: null };

export const WATCHLIST_AGGREGATE: WatchlistAggregate = {
  list_id: "core",
  name: "core",
  days: 30,
  // A cumulative %-from-start curve — starts at 0 by definition, so it can't
  // use `series` (whose clamp band is relative to a non-zero base).
  points: (() => {
    const rand = lcg(777);
    let pct = 0;
    return Array.from({ length: 30 }, (_, i) => {
      pct += (rand() - 0.45) * 1.4;
      return { date: daysBefore(29 - i), pct: round2(pct) };
    });
  })(),
  return_pct: 4.18,
  per_symbol: QUOTE_SEEDS.slice(0, 8).map((s, i) => ({
    symbol: s.symbol,
    return_pct: round2((lcg(3300 + i)() - 0.4) * 18),
  })),
  symbols_missing: ["ZVZZTW"],
};

/** ~140 daily candles ending on the frozen day — enough to fill a chart frame. */
function candles(symbol: string, start: number, n: number, seed: number): BarsResponse {
  const rand = lcg(seed);
  const bars = [];
  let close = start;
  for (let i = n - 1; i >= 0; i -= 1) {
    const open = close;
    const drift = (rand() - 0.47) * start * 0.018;
    close = round2(open + drift);
    const high = round2(Math.max(open, close) + rand() * start * 0.006);
    const low = round2(Math.min(open, close) - rand() * start * 0.006);
    bars.push({
      time: `${daysBefore(i)}T13:30:00Z`,
      open: round2(open),
      high,
      low,
      close,
      volume: Math.round(40_000_000 + rand() * 30_000_000),
    });
  }
  return { symbol, timeframe: "1D", bars, overnight_delayed: false };
}

export function barsFor(symbol: string): BarsResponse {
  const seed =
    [...symbol].reduce((acc, c) => acc + c.charCodeAt(0), 0) * 7919;
  const base = QUOTE_SEEDS.find((q) => q.symbol === symbol)?.price ?? 100;
  return candles(symbol, base * 0.92, 140, seed);
}

export const BARS_EMPTY: BarsResponse = {
  symbol: "SPY",
  timeframe: "1D",
  bars: [],
};

export const SESSION_LEVELS: SessionLevelsResponse = {
  symbol: "SPY",
  session_date: daysBefore(1),
  levels: [
    { name: "POC", price: 621.4, kind: "poc" },
    { name: "PDH", price: 625.88, kind: "prior_high" },
    { name: "PDL", price: 617.12, kind: "prior_low" },
    { name: "PWH", price: 628.44, kind: "prior_week_high" },
    { name: "PWL", price: 611.02, kind: "prior_week_low" },
  ],
};

// ── Options flow ────────────────────────────────────────────────────────────

type FlowSeed = {
  underlying: string;
  right: "C" | "P";
  strike: number;
  dte: number;
  type: string;
  contracts: number;
  premium: number;
  dir: OptionsFlowEvent["direction"];
  time: string;
};

const FLOW_SEEDS: FlowSeed[] = [
  { underlying: "NVDA", right: "P", strike: 165, dte: 7, type: "sweep", contracts: 4_200, premium: 1_884_000, dir: "buy_side", time: "15:41:02" },
  { underlying: "SPY", right: "C", strike: 630, dte: 0, type: "block", contracts: 12_000, premium: 3_120_000, dir: "sell_side", time: "15:22:47" },
  { underlying: "AMD", right: "C", strike: 200, dte: 14, type: "sweep", contracts: 2_880, premium: 1_012_000, dir: "buy_side", time: "14:58:11" },
  { underlying: "TSLA", right: "P", strike: 300, dte: 21, type: "flow_burst", contracts: 1_940, premium: 884_000, dir: "buy_side", time: "14:31:39" },
  { underlying: "BRK.B", right: "C", strike: 530, dte: 45, type: "block", contracts: 620, premium: 402_000, dir: "undetermined", time: "13:55:04" },
  { underlying: "QQQ", right: "P", strike: 550, dte: 3, type: "directional_pressure", contracts: 8_800, premium: 2_244_000, dir: "sell_side", time: "13:12:58" },
  { underlying: "COIN", right: "C", strike: 300, dte: 7, type: "flow_cluster", contracts: 1_120, premium: 318_000, dir: "buy_side", time: "12:44:20" },
  { underlying: "ZVZZTW", right: "C", strike: 5, dte: 30, type: "unusual_volume", contracts: 9_400, premium: 141_000, dir: "undetermined", time: "11:59:07" },
  { underlying: "PLTR", right: "P", strike: 36, dte: 10, type: "sweep", contracts: 3_300, premium: 561_000, dir: "buy_side", time: "11:20:44" },
  { underlying: "MSFT", right: "C", strike: 500, dte: 28, type: "block", contracts: 1_500, premium: 1_140_000, dir: "sell_side", time: "10:48:33" },
  { underlying: "SMCI", right: "C", strike: 60, dte: 14, type: "flow_burst", contracts: 5_600, premium: 448_000, dir: "buy_side", time: "10:14:19" },
  { underlying: "MARA", right: "P", strike: 15, dte: 7, type: "sweep", contracts: 7_100, premium: 284_000, dir: "buy_side", time: "09:52:02" },
];

const FLOW_EVENTS: OptionsFlowEvent[] = FLOW_SEEDS.map((f, i) => {
  const expiry = daysBefore(-f.dte);
  const spot = QUOTE_SEEDS.find((q) => q.symbol === f.underlying)?.price ?? 100;
  const occ = `${f.underlying.replace(".", "")}${expiry.replace(/-/g, "").slice(2)}${f.right}${String(Math.round(f.strike * 1000)).padStart(8, "0")}`;
  return {
    id: `flow_${String(i + 1).padStart(3, "0")}`,
    ts: et(f.time),
    underlying: f.underlying,
    occ_symbol: occ,
    right: f.right,
    strike: f.strike,
    expiry,
    dte: f.dte,
    otm_pct: round2(((f.strike - spot) / spot) * 100),
    event_type: f.type,
    contracts: f.contracts,
    premium_usd: f.premium,
    vol_oi_ratio: round2(1.2 + (i % 6) * 0.73),
    vol_zscore: round2(2.1 + (i % 5) * 0.84),
    direction: f.dir,
    direction_basis: f.dir === "undetermined" ? "no_nbbo" : "tick_test",
    mid_side: f.dir === "buy_side" ? "buy" : f.dir === "sell_side" ? "sell" : "unclassified",
    spread_flag: i % 4 === 0,
    n_prints: 1 + (i % 9),
    exchanges: i % 3 === 0 ? ["CBOE", "PHLX", "ISE"] : ["CBOE"],
    reason: `${f.type.replace(/_/g, " ")} — ${f.contracts.toLocaleString()} contracts vs OI`,
    caveat:
      f.dir === "undetermined"
        ? "no NBBO at print time — side unknown"
        : "aggressor inferred from the tick test, not confirmed",
  };
});

export const OPTIONS_FLOW: OptionsFlowResult = {
  as_of: NOW_ISO,
  count: FLOW_EVENTS.length,
  events: FLOW_EVENTS,
};

export const OPTIONS_FLOW_EMPTY: OptionsFlowResult = {
  as_of: NOW_ISO,
  count: 0,
  events: [],
};

// ── Index pressure ──────────────────────────────────────────────────────────

export const INDEX_PRESSURE: IndexPressureResult = {
  as_of: NOW_ISO,
  date: DAY,
  fallback: false,
  window: "open",
  window_et: "09:30–10:00",
  baseline_days: 20,
  rows: [
    {
      underlying: "SPX",
      bull_usd: 184_220_000,
      bear_usd: 241_880_000,
      unclassified_usd: 38_400_000,
      net_usd: -57_660_000,
      classified_frac: 0.917,
      n_prints: 88_412,
      minutes_with_data: 30,
      last_minute: 30,
      coverage: "full window",
    },
    {
      underlying: "SPXW",
      bull_usd: 96_100_000,
      bear_usd: 88_440_000,
      unclassified_usd: 12_020_000,
      net_usd: 7_660_000,
      classified_frac: 0.939,
      n_prints: 141_902,
      minutes_with_data: 30,
      last_minute: 30,
      coverage: "full window",
    },
    {
      underlying: "VIX",
      bull_usd: 22_400_000,
      bear_usd: 9_880_000,
      unclassified_usd: 4_100_000,
      net_usd: 12_520_000,
      classified_frac: 0.884,
      n_prints: 18_004,
      minutes_with_data: 30,
      last_minute: 30,
      coverage: "full window",
    },
  ],
};

export const INDEX_PRESSURE_EMPTY: IndexPressureResult = {
  as_of: NOW_ISO,
  date: DAY,
  fallback: false,
  window: "open",
  window_et: "09:30–10:00",
  baseline_days: 0,
  rows: [],
};

export const INDEX_PRESSURE_TREND: IndexPressureTrendResult = {
  as_of: NOW_ISO,
  days: Array.from({ length: 12 }, (_, i) => {
    const rand = lcg(4242 + i);
    const bull = Math.round(80_000_000 + rand() * 120_000_000);
    const bear = Math.round(80_000_000 + rand() * 120_000_000);
    return {
      date: daysBefore(11 - i),
      open: {
        bull_usd: bull,
        bear_usd: bear,
        unclassified_usd: Math.round(rand() * 30_000_000),
        net_usd: bull - bear,
        classified_frac: round2(0.86 + rand() * 0.1),
        n_prints: Math.round(60_000 + rand() * 60_000),
      },
      close: {
        bull_usd: Math.round(bull * 0.9),
        bear_usd: Math.round(bear * 1.1),
        unclassified_usd: Math.round(rand() * 20_000_000),
        net_usd: Math.round(bull * 0.9 - bear * 1.1),
        classified_frac: round2(0.88 + rand() * 0.08),
        n_prints: Math.round(50_000 + rand() * 50_000),
      },
      spy_pct: round2((rand() - 0.5) * 2.4),
    };
  }),
};

export const INDEX_PRESSURE_TREND_EMPTY: IndexPressureTrendResult = {
  as_of: NOW_ISO,
  days: [],
};

export const OVERNIGHT_FLOW: OvernightFlowResult = {
  line: {
    session: DAY,
    generated_at: et("04:05:00"),
    window_et: "20:00–04:00",
    slice: {
      prints: 41_882,
      notional: 188_400_000,
      put_share_notional: 0.612,
      zero_dte_share_notional: 0.184,
      macro_window_share: 0.271,
      top_prints: [
        { sym: "SPXW", size: 2_400, price: 12.4, notional: 2_976_000, cond: "a", et: "02:14:07" },
        { sym: "SPX", size: 900, price: 31.2, notional: 2_808_000, cond: "a", et: "23:41:52" },
        { sym: "VIX", size: 5_000, price: 1.85, notional: 925_000, cond: "b", et: "03:02:19" },
      ],
      full_night: { prints: 61_004, notional: 244_100_000, evening_notional: 55_700_000 },
    },
    baseline: {
      nights: 20,
      z: 1.84,
      median: 121_000_000,
      sigma: 36_400_000,
    },
    caveats: [
      "overnight index prints arrive on the Alpaca lane only — Databento OPRA has no GTH",
      "z is against a 20-night baseline; fewer than 20 clean nights widens it",
    ],
  },
  history: Array.from({ length: 14 }, (_, i) => ({
    session: daysBefore(13 - i),
    notional: Math.round(90_000_000 + lcg(999 + i)() * 120_000_000),
    put_share_notional: round2(0.44 + lcg(555 + i)() * 0.3),
  })),
};

export const OVERNIGHT_FLOW_EMPTY: OvernightFlowResult = {
  line: null,
  history: [],
};

// ── Options chain ───────────────────────────────────────────────────────────

function chainRows(spot: number, count: number): OptionsChainRow[] {
  const rand = lcg(31337);
  const step = 5;
  const atm = Math.round(spot / step) * step;
  const rows: OptionsChainRow[] = [];
  for (let i = -Math.floor(count / 2); i < Math.ceil(count / 2); i += 1) {
    const strike = atm + i * step;
    const moneyness = (strike - spot) / spot;
    const callMid = Math.max(0.05, spot * 0.03 - moneyness * spot * 0.42);
    const putMid = Math.max(0.05, spot * 0.03 + moneyness * spot * 0.42);
    rows.push({
      strike,
      call: {
        bid: round2(callMid * 0.98),
        ask: round2(callMid * 1.02),
        last: round2(callMid),
        iv: round2(0.16 + Math.abs(moneyness) * 0.4),
        delta: round2(Math.max(0.01, Math.min(0.99, 0.5 - moneyness * 6))),
        gamma: round2(0.02 * (1 - Math.abs(moneyness) * 4)),
        theta: round2(-0.04 - rand() * 0.03),
        vega: round2(0.3 - Math.abs(moneyness) * 0.9),
        volume: Math.round(rand() * 8_000),
        oi: Math.round(rand() * 42_000),
      },
      put: {
        bid: round2(putMid * 0.98),
        ask: round2(putMid * 1.02),
        last: round2(putMid),
        iv: round2(0.18 + Math.abs(moneyness) * 0.44),
        delta: round2(-Math.max(0.01, Math.min(0.99, 0.5 + moneyness * 6))),
        gamma: round2(0.02 * (1 - Math.abs(moneyness) * 4)),
        theta: round2(-0.05 - rand() * 0.03),
        vega: round2(0.31 - Math.abs(moneyness) * 0.9),
        volume: Math.round(rand() * 9_000),
        oi: Math.round(rand() * 51_000),
      },
    });
  }
  return rows;
}

export const OPTIONS_CHAIN: OptionsChainResult = {
  symbol: "SPY",
  underlying_price: 624.02,
  expirations: [
    daysBefore(0),
    daysBefore(-3),
    daysBefore(-7),
    daysBefore(-14),
    daysBefore(-30),
    daysBefore(-60),
  ],
  expiry: daysBefore(-7),
  rows: chainRows(624.02, 20),
  as_of: NOW_ISO,
  greeks_source: "vendor",
};

export const OPTIONS_CHAIN_EMPTY: OptionsChainResult = {
  symbol: "SPY",
  underlying_price: null,
  expirations: [],
  expiry: null,
  rows: [],
  as_of: null,
};

// ── Scans ───────────────────────────────────────────────────────────────────

export const SCAN_PRESETS: ScanPreset[] = [
  { id: "rvol_spike", label: "RVOL spike", description: "Relative volume vs 20-day", metric_label: "rvol" },
  { id: "above_sma200", label: "Above 200-day", description: "Trend filter", metric_label: "% vs sma200" },
  { id: "oversold_rsi", label: "Oversold RSI", description: "RSI(14) under 30", metric_label: "rsi14" },
  { id: "near_52w_high", label: "Near 52w high", description: "Within 3% of the high", metric_label: "% from high" },
];

const SCAN_ROWS = QUOTE_SEEDS.map((s, i) => ({
  symbol: s.symbol,
  value: round2(3.4 - i * 0.17),
  price: s.price,
  change_pct: s.changePct,
  rsi14: round2(38 + (i % 9) * 4.6),
  vs_sma200_pct: round2(((i % 7) - 3) * 5.4),
  rvol: round2(0.8 + (i % 6) * 0.46),
  dist_52w_high_pct: round2(-((i % 11) * 2.7)),
}));

export const SCAN_RUN: ScanRunResult = {
  preset_id: "rvol_spike",
  preset_label: "RVOL spike",
  metric_label: "rvol",
  universe: "watchlist",
  universe_size: 16,
  count: SCAN_ROWS.length,
  results: SCAN_ROWS,
  as_of: NOW_ISO,
};

export const SCAN_RUN_EMPTY: ScanRunResult = {
  ...SCAN_RUN,
  count: 0,
  results: [],
  note: "no symbols cleared the filter",
};

export const RVOL_SCANNER: RvolScannerResult = {
  as_of: NOW_ISO,
  session: "regular",
  count: 8,
  results: QUOTE_SEEDS.slice(0, 8).map((s, i) => ({
    symbol: s.symbol,
    rvol: round2(3.9 - i * 0.28),
    total_volume: s.volume,
    price: s.price,
  })),
};

export const RVOL_SCANNER_EMPTY: RvolScannerResult = {
  as_of: NOW_ISO,
  session: "closed",
  count: 0,
  results: [],
  note: "market closed — no live RVOL",
};

export { QUOTE_SEEDS };
