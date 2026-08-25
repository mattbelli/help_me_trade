/** Account-shaped fixtures: /api/snapshot, /api/broker, /api/schwab/*, equity. */
import type {
  BrokerInfo,
  Order,
  PortfolioHistory,
  Position,
  SchwabAccountsResult,
  SchwabEquityCurve,
  Snapshot,
} from "@/lib/api";
import type { TradeAccount } from "@/lib/trades-api";

import { DAY, NOW_ISO, daysBefore, et, lcg, round2 } from "./clock";

/** Keeps market_value / unrealized_pl arithmetically consistent with qty+price
 *  so a screenshot never shows a row that contradicts itself. */
function pos(
  symbol: string,
  qty: number,
  avg: number,
  px: number,
  entered: string,
): Position {
  return {
    symbol,
    qty,
    side: "long",
    avg_entry_price: avg,
    current_price: px,
    market_value: round2(qty * px),
    unrealized_pl: round2(qty * (px - avg)),
    entered_at: entered,
  };
}

// Deliberately includes the cases agents miss: a dotted ticker (BRK.B), a
// 6-character ticker (ZVZZTW — the Nasdaq test symbol + a warrant suffix), a
// fractional quantity, and four losers so the red column is exercised.
const MAIN_POSITIONS: Position[] = [
  pos("AAPL", 120, 214.32, 228.71, et("09:41:22", daysBefore(19))),
  pos("NVDA", 60, 182.05, 171.4, et("10:07:03", daysBefore(12))),
  pos("BRK.B", 18, 502.11, 517.86, et("14:22:47", daysBefore(41))),
  pos("ZVZZTW", 900, 3.42, 2.87, et("09:32:10", daysBefore(6))),
  pos("MSFT", 40, 471.9, 489.22, et("11:15:38", daysBefore(27))),
  pos("SPY", 75, 618.4, 624.02, et("09:35:01", daysBefore(3))),
  pos("PLTR", 300, 41.88, 38.14, et("13:48:19", daysBefore(9))),
  pos("AMD", 150, 176.2, 189.77, et("10:52:44", daysBefore(15))),
];

const SECOND_POSITIONS: Position[] = [
  pos("QQQ", 45, 552.7, 561.18, et("09:36:12", daysBefore(5))),
  pos("TSLA", 25, 342.6, 318.05, et("15:01:55", daysBefore(8))),
  pos("SOFI", 1200, 14.22, 15.91, et("09:44:30", daysBefore(22))),
  pos("COIN", 12.5, 288.4, 271.33, et("12:19:07", daysBefore(11))),
  pos("GOOGL", 80, 198.75, 206.4, et("10:11:26", daysBefore(31))),
  pos("MARA", 2000, 18.05, 16.42, et("09:31:44", daysBefore(2))),
  pos("SMCI", 35, 44.9, 51.28, et("14:40:02", daysBefore(17))),
];

const OPEN_ORDERS: Order[] = [
  {
    id: "ord_9f2a41",
    symbol: "AAPL",
    qty: 40,
    side: "sell",
    order_type: "limit",
    status: "new",
    filled_qty: 0,
    // Near the fixture mark (228.71) so the DOM ladder's marker is on-screen.
    limit_price: 228.76,
  },
  {
    id: "ord_5e0aa1",
    symbol: "AAPL",
    qty: 25,
    side: "buy",
    order_type: "limit",
    status: "new",
    filled_qty: 0,
    limit_price: 228.66,
  },
  {
    id: "ord_7c1b08",
    symbol: "ZVZZTW",
    qty: 500,
    side: "buy",
    order_type: "limit",
    status: "partially_filled",
    filled_qty: 220,
    limit_price: 2.75,
  },
  {
    id: "ord_2d5e93",
    symbol: "BRK.B",
    qty: 6,
    side: "buy",
    order_type: "market",
    status: "accepted",
    filled_qty: 0,
    limit_price: null,
  },
  {
    id: "ord_4a8f77",
    symbol: "NVDA",
    qty: 60,
    side: "sell",
    order_type: "stop",
    status: "new",
    filled_qty: 0,
    limit_price: 165.0,
  },
];

// The second paper account carries one order of its own so the orders widget's
// Account column shows two different values, not one repeated down the table.
const SWING_OPEN_ORDERS: Order[] = [
  {
    id: "ord_6b3c12",
    symbol: "TSLA",
    qty: 25,
    side: "sell",
    order_type: "limit",
    status: "pending_new",
    filled_qty: 0,
    limit_price: 325.0,
  },
];

function sumUnrealized(list: Position[]): number {
  return round2(list.reduce((s, p) => s + p.unrealized_pl, 0));
}

function sumValue(list: Position[]): number {
  return round2(list.reduce((s, p) => s + p.market_value, 0));
}

export const SNAPSHOT: Snapshot = {
  captured_at: NOW_ISO,
  broker: "alpaca",
  primary_broker: "alpaca",
  money_type: "paper",
  account: {
    id: "PA3F91KQ",
    status: "ACTIVE",
    cash: 41_882.55,
    buying_power: 83_765.1,
    portfolio_value: sumValue(MAIN_POSITIONS) + 41_882.55,
  },
  position_count: MAIN_POSITIONS.length,
  positions: MAIN_POSITIONS,
  open_order_count: OPEN_ORDERS.length,
  open_orders: OPEN_ORDERS,
  all_accounts_summary: {
    cash: 41_882.55 + 12_940.18,
    buying_power: 83_765.1 + 25_880.36,
    portfolio_value:
      sumValue(MAIN_POSITIONS) +
      sumValue(SECOND_POSITIONS) +
      41_882.55 +
      12_940.18,
    unrealized_pl:
      sumUnrealized(MAIN_POSITIONS) + sumUnrealized(SECOND_POSITIONS),
    account_count: 2,
    money_type: "paper",
  },
  snapshots: [
    {
      broker: "alpaca",
      captured_at: NOW_ISO,
      account: {
        id: "PA3F91KQ",
        status: "ACTIVE",
        cash: 41_882.55,
        buying_power: 83_765.1,
        portfolio_value: sumValue(MAIN_POSITIONS) + 41_882.55,
      },
      position_count: MAIN_POSITIONS.length,
      positions: MAIN_POSITIONS,
      open_order_count: OPEN_ORDERS.length,
      open_orders: OPEN_ORDERS,
      account_id: "acct_paper_main",
      account_label: "paper-main",
      is_default: true,
      money_type: "paper",
    },
    {
      broker: "alpaca",
      captured_at: NOW_ISO,
      account: {
        id: "PA7K22MZ",
        status: "ACTIVE",
        cash: 12_940.18,
        buying_power: 25_880.36,
        portfolio_value: sumValue(SECOND_POSITIONS) + 12_940.18,
      },
      position_count: SECOND_POSITIONS.length,
      positions: SECOND_POSITIONS,
      open_order_count: SWING_OPEN_ORDERS.length,
      open_orders: SWING_OPEN_ORDERS,
      account_id: "acct_paper_swing",
      account_label: "paper-swing",
      is_default: false,
      money_type: "paper",
    },
  ],
};

export const SNAPSHOT_EMPTY: Snapshot = {
  captured_at: NOW_ISO,
  broker: "alpaca",
  primary_broker: "alpaca",
  money_type: "paper",
  account: {
    id: "PA3F91KQ",
    status: "ACTIVE",
    cash: 50_000,
    buying_power: 100_000,
    portfolio_value: 50_000,
  },
  position_count: 0,
  positions: [],
  open_order_count: 0,
  open_orders: [],
  all_accounts_summary: {
    cash: 50_000,
    buying_power: 100_000,
    portfolio_value: 50_000,
    unrealized_pl: 0,
    account_count: 1,
    money_type: "paper",
  },
  snapshots: [
    {
      broker: "alpaca",
      captured_at: NOW_ISO,
      account: {
        id: "PA3F91KQ",
        status: "ACTIVE",
        cash: 50_000,
        buying_power: 100_000,
        portfolio_value: 50_000,
      },
      position_count: 0,
      positions: [],
      open_order_count: 0,
      open_orders: [],
      account_id: "acct_paper_main",
      account_label: "paper-main",
      is_default: true,
      money_type: "paper",
    },
  ],
};

export const BROKER: BrokerInfo = {
  broker: "alpaca",
  primary_broker: "alpaca",
  brokers: {
    alpaca: {
      enabled: true,
      configured: true,
      data_feed: {
        configured: "auto",
        tier: "paid",
        feed: "sip",
        overnight_feed: "boats",
      },
    },
    schwab: {
      enabled: false,
      configured: true,
      auth: {
        authenticated: true,
        message: "token valid",
        estimated_days_until_reauth: 4,
      },
    },
  },
  auth: {
    authenticated: true,
    message: "token valid",
    estimated_days_until_reauth: 4,
  },
};

export const SCHWAB_ACCOUNTS: SchwabAccountsResult = {
  authenticated: true,
  stale: false,
  accounts: [
    {
      id: "schwab_1187",
      last4: "1187",
      label: "brokerage",
      account_type: "CASH",
      is_day_trader: false,
      liquidation_value: 148_302.44,
      cash: 9_211.07,
      long_market_value: 139_091.37,
      day_pl: -1_204.88,
      unrealized_pl: 18_442.19,
      positions: [],
      open_orders: [],
    },
    {
      id: "schwab_4402",
      last4: "4402",
      label: "roth ira",
      account_type: "IRA",
      is_day_trader: false,
      liquidation_value: 61_775.9,
      cash: 2_004.11,
      long_market_value: 59_771.79,
      day_pl: 318.42,
      unrealized_pl: -2_910.55,
      positions: [],
      open_orders: [],
    },
  ],
  totals: {
    liquidation_value: 210_078.34,
    cash: 11_215.18,
    long_market_value: 198_863.16,
    day_pl: -886.46,
    unrealized_pl: 15_531.64,
    account_count: 2,
  },
};

export const SCHWAB_ACCOUNTS_EMPTY: SchwabAccountsResult = {
  authenticated: false,
  message: "not connected",
  accounts: [],
  totals: {},
};

export const TRADE_ACCOUNTS: TradeAccount[] = [
  {
    id: "acct_paper_main",
    label: "paper-main",
    broker: "alpaca",
    created_at: `${daysBefore(210)}T14:02:00Z`,
    last_synced_at: NOW_ISO,
  },
  {
    id: "acct_schwab_1187",
    label: "schwab ••1187",
    broker: "schwab",
    created_at: `${daysBefore(390)}T18:44:00Z`,
    last_synced_at: `${daysBefore(1)}T21:05:00Z`,
  },
];

/** Equity walk: 90 sessions ending slightly below the high — a real drawdown. */
function equityWalk(points: number, start: number, seed: number) {
  const rand = lcg(seed);
  const out: { day: string; equity: number }[] = [];
  let v = start;
  for (let i = points - 1; i >= 0; i -= 1) {
    v *= 1 + (rand() - 0.47) * 0.012;
    out.push({ day: daysBefore(i), equity: round2(v) });
  }
  return out;
}

const WALK = equityWalk(90, 96_500, 20260807);

export const PORTFOLIO_HISTORY: PortfolioHistory = {
  range: "1M",
  points: WALK.map((p) => ({
    timestamp: Math.floor(new Date(`${p.day}T20:00:00Z`).getTime() / 1000),
    equity: p.equity,
    profit_loss: round2(p.equity - WALK[0].equity),
    profit_loss_pct: round2(((p.equity - WALK[0].equity) / WALK[0].equity) * 100),
  })),
};

export const PORTFOLIO_HISTORY_EMPTY: PortfolioHistory = {
  range: "1M",
  points: [],
};

export const SCHWAB_EQUITY_CURVE: SchwabEquityCurve = {
  authenticated: true,
  current_liquidation: 210_078.34,
  skipped_held_days: 0,
  points: WALK.slice(-30).map((p) => ({
    date: p.day,
    equity: round2(p.equity * 2.13),
  })),
};

export const DAY_PNL = { day_pnl: -412.77, has_activity: true };
export const DAY_PNL_EMPTY = { day_pnl: 0, has_activity: false };

export { DAY, MAIN_POSITIONS, SECOND_POSITIONS };
