/** Order/execution/alert/proposal fixtures. */
import type { PendingProposal, PriceAlert } from "@/lib/api";
import type { OrderExecution, TradeEvent } from "@/lib/trades-api";

import { DAY, NOW_ISO, daysBefore, et, money, round2 } from "./clock";

type Fill = {
  day: number;
  time: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
};

// 22 fills across 7 sessions — more than one 560×360 frame holds, so the
// harness exercises the scroll path, not just the first screenful.
const FILLS: Fill[] = [
  { day: 0, time: "15:31:04", symbol: "AAPL", side: "sell", qty: 40, price: 228.71 },
  { day: 0, time: "14:02:19", symbol: "ZVZZTW", side: "buy", qty: 500, price: 2.91 },
  { day: 0, time: "10:14:52", symbol: "NVDA", side: "buy", qty: 60, price: 171.4 },
  { day: 0, time: "09:33:07", symbol: "SPY", side: "buy", qty: 25, price: 622.18 },
  { day: 1, time: "15:48:33", symbol: "BRK.B", side: "buy", qty: 8, price: 515.02 },
  { day: 1, time: "11:27:41", symbol: "PLTR", side: "sell", qty: 150, price: 38.66 },
  { day: 1, time: "09:41:12", symbol: "AMD", side: "buy", qty: 50, price: 187.3 },
  { day: 2, time: "14:55:28", symbol: "TSLA", side: "sell", qty: 25, price: 318.05 },
  { day: 2, time: "12:03:16", symbol: "MSFT", side: "buy", qty: 15, price: 486.44 },
  { day: 2, time: "09:35:50", symbol: "QQQ", side: "buy", qty: 20, price: 558.9 },
  { day: 3, time: "15:57:02", symbol: "COIN", side: "sell", qty: 12.5, price: 271.33 },
  { day: 3, time: "13:22:44", symbol: "MARA", side: "buy", qty: 1000, price: 16.88 },
  { day: 3, time: "10:08:31", symbol: "SOFI", side: "buy", qty: 600, price: 15.04 },
  { day: 4, time: "15:12:19", symbol: "GOOGL", side: "sell", qty: 40, price: 205.77 },
  { day: 4, time: "11:44:03", symbol: "SMCI", side: "buy", qty: 35, price: 46.12 },
  { day: 4, time: "09:31:58", symbol: "AAPL", side: "buy", qty: 60, price: 221.05 },
  { day: 5, time: "14:30:27", symbol: "NVDA", side: "sell", qty: 30, price: 178.92 },
  { day: 5, time: "10:51:39", symbol: "SPY", side: "sell", qty: 50, price: 619.44 },
  { day: 6, time: "15:44:11", symbol: "ZVZZTW", side: "sell", qty: 400, price: 3.18 },
  { day: 6, time: "13:09:05", symbol: "BRK.B", side: "sell", qty: 10, price: 508.9 },
  { day: 6, time: "11:16:47", symbol: "PLTR", side: "buy", qty: 450, price: 40.21 },
  { day: 6, time: "09:32:22", symbol: "AMD", side: "buy", qty: 100, price: 174.6 },
];

export const TRADE_EVENTS: TradeEvent[] = FILLS.map((f, i) => {
  const gross = round2(f.qty * f.price);
  const fees = round2(0.02 + i * 0.011);
  // Buys are cash out (negative), sells cash in (positive) — the widget colors
  // the amount column off this sign, so it must be right.
  const amount = f.side === "buy" ? -round2(gross + fees) : round2(gross - fees);
  return {
    id: `ev_${String(i + 1).padStart(4, "0")}`,
    account_id: "acct_paper_main",
    source: "alpaca",
    event_type: "trade",
    trade_date: daysBefore(f.day),
    symbol: f.symbol,
    side: f.side,
    quantity: String(f.qty),
    price: money(f.price),
    fees: money(fees),
    amount: money(amount),
    description: `${f.side.toUpperCase()} ${f.qty} ${f.symbol} @ ${f.price}`,
    imported_at: NOW_ISO,
    fill_time: et(f.time, daysBefore(f.day)),
  };
});

export const ORDER_EXECUTIONS_REJECTED: OrderExecution[] = [
  {
    id: "exec_a41f",
    symbol: "ZVZZTW",
    side: "buy",
    qty: "1500",
    order_type: "market",
    limit_price: null,
    status: "rejected",
    filled_qty: null,
    avg_fill_price: null,
    execution_mode: "paper",
    submitted_at: et("09:31:02", daysBefore(1)),
    created_at: et("09:31:01", daysBefore(1)),
  },
  {
    id: "exec_b92c",
    symbol: "TSLA",
    side: "sell",
    qty: "60",
    order_type: "limit",
    limit_price: "402.00",
    status: "rejected",
    filled_qty: null,
    avg_fill_price: null,
    execution_mode: "paper",
    submitted_at: et("11:02:44", daysBefore(2)),
    created_at: et("11:02:43", daysBefore(2)),
  },
  {
    id: "exec_c17e",
    symbol: "BRK.B",
    side: "buy",
    qty: "40",
    order_type: "limit",
    limit_price: "530.00",
    status: "rejected",
    filled_qty: null,
    avg_fill_price: null,
    execution_mode: "paper",
    submitted_at: et("14:18:09", daysBefore(4)),
    created_at: et("14:18:08", daysBefore(4)),
  },
];

export const PRICE_ALERTS: PriceAlert[] = [
  {
    id: 1,
    symbol: "SPY",
    price: 630,
    direction: "above",
    note: "breakout over the July high",
    triggered: 0,
    repeat: 1,
    last_triggered_at: null,
    created_at: `${daysBefore(9)}T13:22:00Z`,
  },
  {
    id: 2,
    symbol: "NVDA",
    price: 165,
    direction: "below",
    note: "stop zone — reassess the thesis",
    triggered: 0,
    repeat: 0,
    last_triggered_at: null,
    created_at: `${daysBefore(6)}T15:04:00Z`,
  },
  {
    id: 3,
    symbol: "BRK.B",
    price: 520,
    direction: "above",
    note: "",
    triggered: 1,
    repeat: 1,
    last_triggered_at: et("10:41:12", daysBefore(1)),
    created_at: `${daysBefore(21)}T12:00:00Z`,
  },
  {
    id: 4,
    symbol: "ZVZZTW",
    price: 2.5,
    direction: "below",
    note: "warrant decay watch — very long note that should truncate or wrap cleanly instead of blowing out the row",
    triggered: 0,
    repeat: 0,
    last_triggered_at: null,
    created_at: `${daysBefore(2)}T18:30:00Z`,
  },
  {
    id: 5,
    symbol: "AMD",
    price: 200,
    direction: "above",
    note: "trim into strength",
    triggered: 0,
    repeat: 1,
    last_triggered_at: null,
    created_at: `${daysBefore(14)}T16:12:00Z`,
  },
];

export const PENDING_PROPOSALS: PendingProposal[] = [
  {
    id: "prop_7fa21c",
    summary:
      "Buy 40 AMD at 189.40 — RVOL 2.3× with a hold above the 20-day, stop 176.00, first target 205.00",
    symbol: "AMD",
    side: "buy",
    qty: 40,
    limit_price: 189.4,
    notional: 7576,
    // null, NOT a timestamp: the card renders a live MM:SS countdown against
    // the real clock, so any fixed expiry either ticks (non-reproducible) or
    // reads "Expired". Null hides the line and shows the live card cleanly;
    // the second proposal below keeps a past expiry so the expired styling is
    // still exercised in the same shot.
    expires_at: null,
    payload_hash: "9c1f4a2b7d",
    score: {
      win_prob: 0.58,
      ev_pct: 1.9,
      ci_low: 0.44,
      ci_high: 0.71,
      sufficient: true,
      low_confidence: false,
      base_rate: 0.52,
      brier: 0.21,
      n_samples: 143,
      top_factors: [
        { name: "rvol", direction: "bullish", contribution: 0.11 },
        { name: "trend vs sma200", direction: "bullish", contribution: 0.07 },
        { name: "earnings proximity", direction: "bearish", contribution: -0.04 },
      ],
    },
  },
  {
    id: "prop_2b8e05",
    summary:
      "Sell 25 TSLA at 318.00 — thesis invalidated, close below the entry zone",
    symbol: "TSLA",
    side: "sell",
    qty: 25,
    limit_price: 318,
    notional: 7950,
    expires_at: et("16:00:00"),
    payload_hash: "3e77b1c0aa",
    score: {
      win_prob: 0.49,
      ev_pct: -0.3,
      sufficient: false,
      low_confidence: true,
      n_samples: 22,
      basis: "strategy_backtest",
      strategy_id: "mean_revert_v2",
      label: "mean reversion v2",
      validated: "unvalidated",
      note: "sample below the calibration floor — treat as perception",
    },
  },
];

export const KILL_SWITCH = { enabled: false, reason: null, updated_at: NOW_ISO };
export const ENTRIES_HALT = { halted: false, reason: null, updated_at: NOW_ISO };

export { DAY };
