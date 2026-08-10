/** Performance fixtures: dashboard, calendar, sectors, per-symbol stats. */
import type {
  DailyPnlStat,
  PerformanceDashboard,
  RoundTrip,
  SectorBreakdown,
} from "@/lib/performance-api";
import type { DailyTradeStat, SymbolTradeStat } from "@/lib/trades-api";

import { DAY, daysBefore, lcg, money, round2 } from "./clock";

const SYMBOLS = [
  "AAPL",
  "NVDA",
  "BRK.B",
  "ZVZZTW",
  "MSFT",
  "SPY",
  "PLTR",
  "AMD",
  "TSLA",
  "COIN",
  "SOFI",
  "GOOGL",
];

/** 65 sessions of realized P&L — a real equity curve with losing stretches. */
const DAILY: { date: string; net: number; events: number }[] = (() => {
  const rand = lcg(884422);
  const out: { date: string; net: number; events: number }[] = [];
  for (let i = 64; i >= 0; i -= 1) {
    const date = daysBefore(i);
    const weekday = new Date(`${date}T12:00:00Z`).getUTCDay();
    if (weekday === 0 || weekday === 6) continue; // no weekend sessions
    const r = rand();
    // Fat-ish tails: mostly small, occasionally a real winner or a real loss.
    const net = round2((r - 0.46) * (r > 0.93 || r < 0.05 ? 3800 : 900));
    out.push({ date, net, events: 1 + Math.floor(rand() * 6) });
  }
  return out;
})();

const DAILY_REALIZED: DailyPnlStat[] = DAILY.map((d) => ({
  date: d.date,
  net_amount: money(d.net),
  realized: money(d.net),
  realized_avg: money(round2(d.net * 0.97)),
  avg_approx: false,
  event_count: d.events,
}));

const DAILY_CASH_FLOW: DailyPnlStat[] = DAILY.map((d) => ({
  date: d.date,
  net_amount: money(round2(d.net * 1.12)),
  event_count: d.events,
}));

const ROUND_TRIPS: RoundTrip[] = (() => {
  const rand = lcg(112233);
  return Array.from({ length: 44 }, (_, i) => {
    const hold = 1 + Math.floor(rand() * 26);
    const openDay = 3 + Math.floor(rand() * 55);
    const pnl = round2((rand() - 0.44) * 2400);
    return {
      symbol: SYMBOLS[i % SYMBOLS.length],
      open_date: daysBefore(openDay),
      close_date: daysBefore(Math.max(0, openDay - hold)),
      quantity: String(10 + Math.floor(rand() * 400)),
      pnl: money(pnl),
      is_option: i % 5 === 0,
    };
  });
})();

const sorted = [...ROUND_TRIPS].sort(
  (a, b) => parseFloat(b.pnl) - parseFloat(a.pnl),
);

const netTotal = round2(DAILY.reduce((s, d) => s + d.net, 0));
const wins = ROUND_TRIPS.filter((r) => parseFloat(r.pnl) > 0).length;

export const PERFORMANCE_DASHBOARD: PerformanceDashboard = {
  period: "quarter",
  from_date: daysBefore(64),
  to_date: DAY,
  summary: {
    event_count: DAILY.reduce((s, d) => s + d.events, 0),
    trade_count: ROUND_TRIPS.length * 2,
    net_amount: money(netTotal),
    total_fees: money(184.22),
    round_trips: ROUND_TRIPS.length,
    wins,
    losses: ROUND_TRIPS.length - wins,
    win_rate: round2(wins / ROUND_TRIPS.length),
    from_date: daysBefore(64),
    to_date: DAY,
  },
  realized_pnl: money(netTotal),
  cash_flow_pnl: money(round2(netTotal * 1.12)),
  daily_realized: DAILY_REALIZED,
  daily_cash_flow: DAILY_CASH_FLOW,
  round_trips: ROUND_TRIPS,
  stock_win_rate: round2(wins / ROUND_TRIPS.length),
  asset_classes: {
    stock_trades: 71,
    option_trades: 17,
    other_events: 9,
    stock_pct: 0.807,
    option_pct: 0.193,
  },
  day_of_week: ["Mon", "Tue", "Wed", "Thu", "Fri"].map((day, i) => ({
    day,
    net_amount: money(round2((i - 2) * 812.4)),
    trade_count: 12 + i * 3,
  })),
  day_of_week_realized: ["Mon", "Tue", "Wed", "Thu", "Fri"].map((day, i) => ({
    day,
    net_amount: money(round2((i - 2.4) * 744.1)),
    trade_count: 12 + i * 3,
  })),
  top_winners: sorted.slice(0, 5),
  top_losers: sorted.slice(-5).reverse(),
};

export const PERFORMANCE_DASHBOARD_EMPTY: PerformanceDashboard = {
  period: "quarter",
  from_date: null,
  to_date: null,
  summary: {
    event_count: 0,
    trade_count: 0,
    net_amount: "0.00",
    total_fees: "0.00",
    round_trips: 0,
    wins: 0,
    losses: 0,
    win_rate: null,
  },
  realized_pnl: "0.00",
  cash_flow_pnl: "0.00",
  daily_realized: [],
  daily_cash_flow: [],
  round_trips: [],
  stock_win_rate: null,
  asset_classes: {
    stock_trades: 0,
    option_trades: 0,
    other_events: 0,
    stock_pct: null,
    option_pct: null,
  },
  day_of_week: [],
  day_of_week_realized: [],
  top_winners: [],
  top_losers: [],
};

/** Slice the dashboard to a from/to window, the way the real endpoint does. */
export function dashboardForRange(
  from: string | null,
  to: string | null,
): PerformanceDashboard {
  if (!from && !to) return PERFORMANCE_DASHBOARD;
  const inRange = (d: { date: string }) =>
    (!from || d.date >= from) && (!to || d.date <= to);
  const realized = DAILY_REALIZED.filter(inRange);
  const total = round2(
    realized.reduce((s, d) => s + parseFloat(d.realized_avg ?? d.net_amount), 0),
  );
  return {
    ...PERFORMANCE_DASHBOARD,
    period: "custom",
    from_date: from,
    to_date: to,
    daily_realized: realized,
    daily_cash_flow: DAILY_CASH_FLOW.filter(inRange),
    realized_pnl: money(total),
    cash_flow_pnl: money(round2(total * 1.12)),
  };
}

export const PERFORMANCE_CALENDAR: DailyTradeStat[] = DAILY.map((d) => ({
  date: d.date,
  net_amount: money(d.net),
  event_count: d.events,
}));

export const PERFORMANCE_SYMBOLS: SymbolTradeStat[] = SYMBOLS.map((s, i) => ({
  symbol: s,
  trade_count: 3 + ((i * 5) % 17),
  net_amount: money(round2((6 - i) * 418.7)),
}));

export const PERFORMANCE_SECTORS: SectorBreakdown = {
  as_of: `${DAY}T20:00:00Z`,
  // weight_pct is a FRACTION (0..1) — the backend's own test asserts the
  // sectors + unclassified sum to ~1.0, and the widget renders `× 100`.
  // Feeding it 41.2 rendered "4120.0%" and collapsed the radar to a sliver.
  sectors: [
    { sector: "Technology", weight_pct: 0.412, market_value: "88420.11", unrealized_pl: "6218.40", realized_pnl: "4120.88", position_count: 5, symbols: ["AAPL", "NVDA", "MSFT", "AMD", "SMCI"] },
    { sector: "Financials", weight_pct: 0.187, market_value: "40118.22", unrealized_pl: "-1204.77", realized_pnl: "902.14", position_count: 3, symbols: ["BRK.B", "SOFI", "COIN"] },
    { sector: "Communication Services", weight_pct: 0.124, market_value: "26610.05", unrealized_pl: "612.00", realized_pnl: "-318.42", position_count: 1, symbols: ["GOOGL"] },
    { sector: "Consumer Discretionary", weight_pct: 0.101, market_value: "21668.90", unrealized_pl: "-2455.00", realized_pnl: "-1102.60", position_count: 2, symbols: ["TSLA", "AMZN"] },
    { sector: "ETF / Index", weight_pct: 0.14, market_value: "30044.18", unrealized_pl: "812.44", realized_pnl: "2210.05", position_count: 2, symbols: ["SPY", "QQQ"] },
    { sector: "Crypto miners", weight_pct: 0.024, market_value: "7722.00", unrealized_pl: "-3260.00", realized_pnl: "-880.11", position_count: 1, symbols: ["MARA"] },
  ],
  unclassified: {
    symbols: ["ZVZZTW"],
    market_value: "2583.00",
    realized_pnl: "-495.00",
  },
};

export const PERFORMANCE_SECTORS_EMPTY: SectorBreakdown = {
  as_of: `${DAY}T20:00:00Z`,
  sectors: [],
  unclassified: { symbols: [], market_value: "0.00", realized_pnl: "0.00" },
};
