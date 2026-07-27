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
  // Present on scoped (account_id) responses — snapshot-backed window P&L.
  // realized = change − unrealized_change (net-cash decomposition, serv-side).
  account_id?: string;
  start_equity?: number;
  end_equity?: number;
  change?: number;
  realized?: number;
  unrealized_change?: number;
}

/** Realized (ET-day, net-cash) day P&L for a scope — matches the web toolbar DAY tile. */
export interface DayPnl {
  day_pnl: number;
  has_activity: boolean;
  scope_fallback: boolean;
}

export type PortfolioRange = '1D' | '3D' | '1W' | '1M' | '1Y' | 'ALL';

export interface Bar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** Relative volume vs the 20-day time-of-day profile. value is null while "warming". */
export interface RvolInfo {
  value: number | null;
  state: 'warming' | 'live' | 'settled' | 'overnight';
}

export interface WatchlistQuote {
  price: number;
  change_pct: number;
  sparkline: number[];
  /** Today's RTH 5-min closes, chronological from the 09:30 open (78 bars = full day). */
  intraday?: number[];
  prev_close?: number;
  volume?: number;
  rvol?: RvolInfo | null;
  // Extended-hours (serv build in progress — treat as absent until deployed):
  market_session?: 'pre' | 'regular' | 'post' | 'overnight' | 'closed';
  ext_price?: number | null;
  ext_change_pct?: number | null;
  day_high?: number;
  day_low?: number;
  // Top-of-book (WS stream only — the REST poll doesn't carry these; merged in
  // by cockpit-pager's onQuote. 0 = no quote, e.g. Alpaca-only symbols):
  bid?: number;
  ask?: number;
  // Company info (finnhub, folded into the quote by serv):
  pe?: number | null;
  eps?: number | null;
  div_yield?: number | null;
  market_cap?: number | null;
  last_earnings?: string | null;
  next_earnings?: string | null;
}

export interface WatchlistMarket {
  quotes: Record<string, WatchlistQuote>;
}

/** One past quarter's EPS surprise. surprise_pct > 0 = beat, < 0 = miss. */
export interface EarningsEvent {
  period: string; // "YYYY-MM-DD"
  label: string; // "Q2'26"
  eps_actual: number | null;
  eps_estimate: number | null;
  surprise_pct: number | null;
}

export interface EarningsHistory {
  symbol: string;
  events: EarningsEvent[]; // most-recent first
}

// --- /api/ticker/{symbol} (api/ticker_routes.py; mirrors frontend/lib/ticker-info-api.ts) ---

/** Company profile (Finnhub /stock/profile2, trimmed server-side). Best-effort:
 *  every field is optional — a missing Finnhub key returns {}. */
export interface TickerProfile {
  name?: string;
  ticker?: string;
  exchange?: string;
  finnhubIndustry?: string;
  marketCapitalization?: number; // in millions of the reporting currency
  logo?: string;
  weburl?: string;
  ipo?: string;
  shareOutstanding?: number; // in millions
  country?: string;
  currency?: string;
}

/** Curated fundamentals (Finnhub /stock/metric, flattened server-side). Margins,
 *  yields and growth arrive as percentages already (45.3 = 45.3%). */
export interface TickerMetrics {
  pe_ttm?: number;
  eps_ttm?: number;
  week_52_high?: number;
  week_52_low?: number;
  beta?: number;
  div_yield?: number;
  gross_margin?: number;
  net_margin?: number;
  market_cap?: number; // in millions
  ps_ttm?: number;
  roe_ttm?: number;
  revenue_growth_yoy?: number;
}

export interface TickerNewsItem {
  headline: string;
  source: string;
  url: string;
  datetime: string | null; // ISO 8601
  summary: string;
  image: string;
}

/** GET /api/ticker/{symbol}. `facts` is the app's own per-symbol context
 *  (ai.perception.context) — left untyped until a mobile screen needs it. */
export interface TickerInfo {
  symbol: string;
  profile: TickerProfile;
  metrics: TickerMetrics;
  news: TickerNewsItem[];
  facts?: Record<string, unknown>;
}

export interface WatchlistList {
  id: string;
  name: string;
  symbols: string[];
}

export interface WatchlistsState {
  active_id: string;
  lists: WatchlistList[];
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
  /** Human-readable account/book name (e.g. "Fable (LLM book)"); fail-soft nullable. */
  account_label?: string | null;
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

export interface FeedNotification {
  id: string;
  kind: string;
  title: string;
  body: string;
  severity: string;
  account_id: string | null;
  strategy_id: string | null;
  ref_type: string | null;
  ref_id: string | null;
  action_url: string | null;
  group_key: string | null;
  data?: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
  dismissed_at: string | null;
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
  // Price-alert fields (source === 'price_alert'):
  alert_id?: number | null;
  target_price?: number | null;
  trigger_price?: number | null;
  direction?: string | null; // "above" | "below" | "cross"
  note?: string | null;
}

export interface PriceAlert {
  id: number;
  symbol: string;
  price: number;
  direction: string; // "above" | "below" | "cross"
  note?: string;
  triggered?: number; // 0/1
  repeat?: number; // 0/1 — persistent: re-arms after each crossing instead of one-shot
  created_at?: string;
}

export interface HealthStatus {
  status: string;
  poll_interval_seconds?: number;
  last_poll_age_seconds?: number;
  poll_healthy?: boolean;
}

export interface Report {
  id: string;
  kind: string;
  symbol: string;
  title: string;
  body: string;
  format: 'text' | 'html' | 'markdown';
  payload: Record<string, unknown>;
  generated_at: string;
}

export interface ResearchArtifact {
  id: number;
  topic: string;
  symbol: string | null;
  markdown: string;
  status: string;
  depth: string;
  html: string | null;
  sources: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
}

export interface ReportsSettings {
  morning_levels_enabled: boolean;
  hour_et: number;
}

export interface ResearchSettings {
  searxng_url: string;
  effort: string;
}

export interface AppSettings {
  reports?: ReportsSettings;
  research?: ResearchSettings;
}

export type StrategyEntryMode = 'auto' | 'propose';
export type StrategyExitMode = 'auto' | 'propose';

export interface Strategy {
  id: string;
  name: string;
  enabled: boolean;
  account_id: string;
  account_label: string;
  notional_usd: number;
  entry_mode: StrategyEntryMode;
  exit_mode: StrategyExitMode;
  universe: string;
  max_concurrent_positions: number | null;
  max_deployed_usd: number | null;
  alert_style: string;
  params?: Record<string, unknown>;
}

/** A persisted chat session (list row). */
export interface ChatSession {
  id: string;
  title: string;
  mode: string;
  created_at: string;
  updated_at: string;
  pinned: boolean;
}

/** One stored message in a session. `parts` is an AI-SDK-style array; text
 *  lives in `{type:'text', text}` entries — join those for the content. */
export interface ChatSessionMessage {
  id: string;
  role: 'user' | 'assistant';
  parts: Array<Record<string, unknown>>;
  created_at: string;
}

/** A session plus its full transcript (GET /api/chat/sessions/{id}). */
export interface ChatSessionDetail extends ChatSession {
  messages: ChatSessionMessage[];
}

export interface WebCustomTheme {
  id: string;
  name: string;
  base: string;
  colorScheme: string;
  tokens: {
    bg?: string;
    surface?: string;
    border?: string;
    fg?: string;
    fgDim?: string;
    accent?: string;
    green?: string;
    red?: string;
    chartAccent?: string;
    chartBg?: string;
  };
}
