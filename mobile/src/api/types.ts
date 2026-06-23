export interface Account {
  id?: string;
  status: string;
  cash: number;
  buying_power: number;
  portfolio_value: number;
}

export interface Position {
  symbol: string;
  qty: number;
  side: string;
  market_value: number;
  unrealized_pl: number;
  avg_entry_price: number;
  current_price: number;
}

export interface Order {
  id: string;
  symbol: string;
  qty: number;
  side: string;
  order_type: string;
  status: string;
  filled_qty: number;
  limit_price: number | null;
}

export interface AllAccountsSummary {
  cash: number;
  buying_power: number;
  portfolio_value: number;
  unrealized_pl: number;
  account_count: number;
}

export interface BrokerSnapshot {
  broker: string;
  captured_at: string;
  account: Account;
  position_count: number;
  positions: Position[];
  open_order_count: number;
  open_orders: Order[];
  account_id?: string | null;
  account_label?: string | null;
  is_default?: boolean;
}

export interface Snapshot {
  captured_at: string;
  broker?: string;
  primary_broker?: string;
  account: Account;
  position_count: number;
  positions: Position[];
  open_order_count: number;
  open_orders: Order[];
  snapshots?: BrokerSnapshot[];
  all_accounts_summary?: AllAccountsSummary;
}

export interface PortfolioHistoryPoint {
  timestamp: number;
  equity: number;
  profit_loss: number;
  profit_loss_pct: number;
}

export interface PortfolioHistory {
  range: string;
  points: PortfolioHistoryPoint[];
}

export type PortfolioRange = '1D' | '3D' | '1W' | '1M' | '1Y' | 'ALL';

export interface WatchlistQuote {
  price: number;
  change_pct: number;
  sparkline: number[];
  prev_close?: number;
  volume?: number;
  day_high?: number;
  day_low?: number;
}

export interface WatchlistMarket {
  quotes: Record<string, WatchlistQuote>;
}

export interface ProposalScoreFactor {
  name: string;
  direction: 'bullish' | 'bearish' | string;
  contribution: number;
}

export interface ProposalScore {
  win_prob: number;
  ev_pct: number;
  ci_low: number;
  ci_high: number;
  sufficient: boolean;
  low_confidence: boolean;
  base_rate: number;
  brier: number;
  n_samples: number;
  top_factors: ProposalScoreFactor[];
}

export interface PendingProposal {
  id: string;
  symbol: string;
  side: string;
  qty: number;
  order_type: string;
  limit_price: number | null;
  status: string;
  summary: string;
  created_at?: string;
  expires_at: string | null;
  payload_hash: string;
  account_id?: string | null;
  source?: string | null;
  score?: ProposalScore | null;
  proposal?: Record<string, unknown>;
}

export interface AlertAction {
  type?: string | null;
  proposal_id?: string | null;
  summary?: string | null;
  expires_at?: string | null;
  message?: string | null;
}

export interface MarketAlert {
  id: string;
  ts: string;
  source?: string;
  rule_id?: string | null;
  rule_name?: string | null;
  action?: AlertAction | null;
  symbol?: string | null;
  price?: number | null;
}

export interface HealthStatus {
  status: string;
  poll_interval_seconds?: number;
  last_poll_age_seconds?: number;
  poll_healthy?: boolean;
}
