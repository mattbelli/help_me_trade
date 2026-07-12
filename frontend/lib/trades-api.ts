import { apiFetch, API_BASE } from "@/lib/api";

export type TradeAccount = {
  id: string;
  label: string;
  broker: string;
  created_at: string;
  last_synced_at?: string | null;
};

export type TradeEvent = {
  id: string;
  account_id: string;
  source: string;
  event_type: string;
  trade_date: string;
  symbol: string | null;
  side: string | null;
  quantity: string | null;
  price: string | null;
  fees: string;
  amount: string;
  description: string;
  imported_at: string;
  /** Real broker fill timestamp (ISO) when the row carries one; null for
   * date-only import rows. */
  fill_time?: string | null;
};

export type TradeSummary = {
  event_count: number;
  trade_count: number;
  net_amount: string;
  total_fees: string;
  round_trips: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  from_date?: string | null;
  to_date?: string | null;
};

export type DailyTradeStat = {
  date: string;
  net_amount: string;
  event_count: number;
};

export type SymbolTradeStat = {
  symbol: string;
  trade_count: number;
  net_amount: string;
};

export type ImportPreview = {
  from_date: string | null;
  to_date: string | null;
  parsed: number;
  would_insert: number;
  duplicates: number;
  skipped: number;
  warnings: string[];
  sample: Array<{
    trade_date: string;
    event_type: string;
    symbol: string | null;
    side: string | null;
    quantity: string | null;
    price: string | null;
    fees: string;
    amount: string;
    description: string;
  }>;
};

export type ImportResult = {
  batch_id: string;
  parsed: number;
  inserted: number;
  duplicates: number;
  skipped: number;
  warnings: string[];
  from_date: string | null;
  to_date: string | null;
};

export async function fetchTradeAccounts(): Promise<TradeAccount[]> {
  const res = await apiFetch(`${API_BASE}/api/trades/accounts`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.accounts ?? [];
}

export async function createTradeAccount(
  label: string,
  broker: string,
): Promise<TradeAccount> {
  const res = await apiFetch(`${API_BASE}/api/trades/accounts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, broker }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.account;
}

export async function fetchTradeEvents(params: {
  account_id?: string;
  from_date?: string;
  to_date?: string;
  symbol?: string;
  limit?: number;
}): Promise<TradeEvent[]> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  if (params.symbol) qs.set("symbol", params.symbol);
  if (params.limit) qs.set("limit", String(params.limit));
  const res = await apiFetch(`${API_BASE}/api/trades/events?${qs}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.events ?? [];
}

export async function fetchTradeSummary(params: {
  account_id?: string;
  from_date?: string;
  to_date?: string;
}): Promise<TradeSummary> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  const res = await apiFetch(`${API_BASE}/api/trades/stats/summary?${qs}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchDailyTradeStats(params: {
  account_id?: string;
  from_date?: string;
  to_date?: string;
}): Promise<DailyTradeStat[]> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  const res = await apiFetch(`${API_BASE}/api/trades/stats/daily?${qs}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.days ?? [];
}

export async function fetchSymbolTradeStats(params: {
  account_id?: string;
  from_date?: string;
  to_date?: string;
}): Promise<SymbolTradeStat[]> {
  const qs = new URLSearchParams();
  if (params.account_id) qs.set("account_id", params.account_id);
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  const res = await apiFetch(`${API_BASE}/api/trades/stats/by-symbol?${qs}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.symbols ?? [];
}

export async function previewTradeImport(
  file: File,
  accountId?: string,
): Promise<ImportPreview> {
  const form = new FormData();
  form.append("file", file);
  if (accountId) form.append("account_id", accountId);
  const res = await apiFetch(`${API_BASE}/api/trades/import/preview`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error ?? `API error ${res.status}`);
  }
  return res.json();
}

export async function importTrades(
  file: File,
  accountId: string,
): Promise<ImportResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("account_id", accountId);
  const res = await apiFetch(`${API_BASE}/api/trades/import`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error ?? `API error ${res.status}`);
  }
  return res.json();
}

export async function requestTradeReview(body: {
  account_id?: string;
  symbol?: string;
  from_date?: string;
  to_date?: string;
  focus?: string;
}): Promise<{ review: string; stats: TradeSummary; trade_count: number }> {
  const res = await apiFetch(`${API_BASE}/api/trades/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error ?? err.detail ?? `API error ${res.status}`);
  }
  return res.json();
}

export type OrderExecution = {
  id: string;
  symbol: string;
  side: string;
  qty: string;
  order_type: string;
  limit_price: string | null;
  status: string;
  filled_qty: string | null;
  avg_fill_price: string | null;
  execution_mode: string;
  submitted_at: string | null;
  created_at: string;
};

export async function fetchOrderExecutions(params: {
  status?: string;
  limit?: number;
  account_id?: string;
}): Promise<{ executions: OrderExecution[]; count: number }> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.account_id) qs.set("account_id", params.account_id);
  const res = await apiFetch(`${API_BASE}/api/orders/executions?${qs}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export function parseMoney(value: string | null | undefined): number {
  if (!value) return 0;
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : 0;
}

export function formatTradeMoney(value: string | number): string {
  const n = typeof value === "string" ? parseMoney(value) : value;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(n);
}
