import { apiFetch, API_BASE } from "@/lib/api";
import type { DailyTradeStat, SymbolTradeStat, TradeSummary } from "@/lib/trades-api";

export type PerformancePeriod =
  | "day"
  | "week"
  | "month"
  | "quarter"
  | "year"
  | "all"
  | "custom";

export type RoundTrip = {
  symbol: string;
  open_date: string;
  close_date: string;
  quantity: string;
  pnl: string;
  is_option: boolean;
};

export type AssetClassBreakdown = {
  stock_trades: number;
  option_trades: number;
  other_events: number;
  stock_pct: number | null;
  option_pct: number | null;
};

export type DayOfWeekStat = {
  day: string;
  net_amount: string;
  trade_count: number;
};

export type DailyPnlStat = {
  date: string;
  net_amount: string;
  event_count: number;
};

export type PnlMode = "realized" | "cashflow";

export type PerformanceDashboard = {
  period: string;
  from_date: string | null;
  to_date: string | null;
  summary: TradeSummary;
  realized_pnl: string;
  cash_flow_pnl: string;
  daily_realized: DailyPnlStat[];
  daily_cash_flow: DailyPnlStat[];
  round_trips: RoundTrip[];
  stock_win_rate: number | null;
  asset_classes: AssetClassBreakdown;
  day_of_week: DayOfWeekStat[];
  day_of_week_realized: DayOfWeekStat[];
  top_winners: RoundTrip[];
  top_losers: RoundTrip[];
};

export type SyncResult = {
  broker: string;
  fetched?: number;
  parsed?: number;
  inserted?: number;
  duplicates?: number;
  error?: string;
  note?: string;
};

export async function fetchPerformanceDashboard(params: {
  account_id?: string;
  period?: PerformancePeriod;
  from_date?: string;
  to_date?: string;
}): Promise<PerformanceDashboard> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.period) qs.set("period", params.period);
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  const res = await apiFetch(
    `${API_BASE}/api/performance/dashboard?${qs}`,
  );
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchPerformanceCalendar(params: {
  account_id?: string;
  from_date?: string;
  to_date?: string;
}): Promise<DailyTradeStat[]> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  const res = await apiFetch(
    `${API_BASE}/api/performance/calendar?${qs}`,
  );
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.days ?? [];
}

export async function fetchPerformanceSymbols(params: {
  account_id?: string;
  period?: PerformancePeriod;
  from_date?: string;
  to_date?: string;
}): Promise<SymbolTradeStat[]> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.period) qs.set("period", params.period);
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  const res = await apiFetch(
    `${API_BASE}/api/performance/symbols?${qs}`,
  );
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.symbols ?? [];
}

export async function syncTrades(): Promise<{ results: SyncResult[] }> {
  const res = await apiFetch(`${API_BASE}/api/performance/sync`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export type RetrospectiveResult = {
  artifact_id: number;
  period: string;
  n_proposed_rules: number;
  n_lessons: number;
};

export async function runTradeRetrospective(params: {
  account_id?: string;
  period?: PerformancePeriod;
}): Promise<RetrospectiveResult> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.period) qs.set("period", params.period);
  const res = await apiFetch(`${API_BASE}/api/performance/retrospective?${qs}`, {
    method: "POST",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `API error ${res.status}`);
  }
  return res.json();
}

export type SectorSlice = {
  sector: string;
  weight_pct: number;
  market_value: string;
  unrealized_pl: string;
  realized_pnl: string;
  position_count: number;
  symbols: string[];
};

export type SectorBreakdown = {
  as_of: string;
  sectors: SectorSlice[];
  unclassified: {
    symbols: string[];
    market_value: string;
    realized_pnl: string;
  };
};

export async function fetchPerformanceSectors(params: {
  account_id?: string;
  period?: PerformancePeriod;
}): Promise<SectorBreakdown> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.period) qs.set("period", params.period);
  const res = await apiFetch(`${API_BASE}/api/performance/sectors?${qs}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
