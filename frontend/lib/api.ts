import type { AiSettings } from "./ai-settings";
import type { CustomTheme } from "./workspace/custom-themes";
import type { WorkspaceLayoutItem, WorkspacePagesState } from "./workspace/layout-persistence";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";

export function apiAuthHeaders(
  extra?: HeadersInit,
  tokenOverride?: string,
): HeadersInit {
  const headers = new Headers(extra);
  // `||` not `??`: an empty-string override (untouched admin field) must fall
  // through to API_TOKEN, else the normal token is silently dropped.
  const token = tokenOverride || API_TOKEN;
  if (token) {
    headers.set("X-API-Token", token);
  }
  return headers;
}

export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs = 20_000,
  tokenOverride?: string,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, {
      ...init,
      signal: init?.signal ?? controller.signal,
      headers: apiAuthHeaders(init?.headers, tokenOverride),
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        "API request timed out — is the backend running on port 8000?",
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function apiUrlWithToken(path: string): string {
  const isAbsolute = /^(https?|wss?):\/\//i.test(path);
  const base = isAbsolute
    ? path
    : `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  if (!API_TOKEN) return base;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}token=${encodeURIComponent(API_TOKEN)}`;
}

export function marketWsUrl(): string {
  return apiUrlWithToken(`${API_BASE.replace(/^http/, "ws")}/ws/market`);
}

export type Account = {
  id: string;
  status: string;
  cash: number;
  buying_power: number;
  portfolio_value: number;
};

export type Position = {
  symbol: string;
  qty: number;
  side: string;
  market_value: number;
  unrealized_pl: number;
  avg_entry_price: number;
  current_price: number;
};

export type Order = {
  id: string;
  symbol: string;
  qty: number;
  side: string;
  order_type: string;
  status: string;
  filled_qty: number;
  limit_price: number | null;
};

export type AllAccountsSummary = {
  cash: number;
  buying_power: number;
  portfolio_value: number;
  unrealized_pl: number;
  account_count: number;
  money_type?: "paper" | "real";
};

export type Snapshot = {
  captured_at: string;
  broker?: string;
  primary_broker?: string;
  money_type?: "paper" | "real";
  account: Account;
  position_count: number;
  positions: Position[];
  open_order_count: number;
  open_orders: Order[];
  snapshots?: BrokerSnapshot[];
  all_accounts_summary?: AllAccountsSummary;
};

export type BrokerSnapshot = {
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
  money_type?: "paper" | "real";
};

export type BrokerInfo = {
  broker: string;
  primary_broker?: string;
  brokers?: Record<
    string,
    {
      enabled: boolean;
      configured: boolean;
      auth?: {
        authenticated: boolean;
        message: string;
        estimated_days_until_reauth?: number | null;
      };
    }
  >;
  auth?: {
    authenticated: boolean;
    message: string;
    estimated_days_until_reauth?: number | null;
  };
};

export async function fetchBroker(): Promise<BrokerInfo> {
  const res = await apiFetch(`${API_BASE}/api/broker`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchSnapshot(): Promise<Snapshot> {
  const res = await apiFetch(`${API_BASE}/api/snapshot`, { cache: "no-store" });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error(
        "API authentication failed — ensure NEXT_PUBLIC_API_TOKEN in frontend/.env.local matches API_TOKEN in .env",
      );
    }
    throw new Error(
      res.status === 503
        ? "Waiting for first snapshot from the API..."
        : `API error ${res.status}`,
    );
  }
  return res.json();
}

export function formatMoney(value: number): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// Share quantity: whole shares show clean (48), fractional caps at 3 decimals
// (0.871166 -> 0.871) with trailing zeros dropped. Display only — full precision
// stays in the data.
export function formatQty(value: number): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: 3,
    useGrouping: false,
  });
}

export type PnlRange = "1D" | "3D" | "1W" | "1M" | "1Y" | "ALL";

export type PortfolioHistoryPoint = {
  timestamp: number;
  equity: number;
  profit_loss: number;
  profit_loss_pct: number;
};

export type PortfolioHistory = {
  range: PnlRange;
  points: PortfolioHistoryPoint[];
};

export type StockBar = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type BarsResponse = {
  symbol: string;
  timeframe: string;
  bars: StockBar[];
};

export async function fetchPortfolioHistory(
  range: PnlRange,
): Promise<PortfolioHistory> {
  const res = await apiFetch(
    `${API_BASE}/api/portfolio/history?range=${encodeURIComponent(range)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ??
        `Portfolio history error ${res.status}`,
    );
  }
  return res.json();
}

export async function fetchBars(
  symbol: string,
  options?: { timeframe?: string; days?: number },
): Promise<BarsResponse> {
  const params = new URLSearchParams({ symbol });
  if (options?.timeframe) params.set("timeframe", options.timeframe);
  if (options?.days) params.set("days", String(options.days));

  const res = await apiFetch(`${API_BASE}/api/bars?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Bars error ${res.status}`,
    );
  }
  return res.json();
}

export const SECRET_MASK = "********";

export type ResearchModelRoles = {
  planner?: string;
  specialists?: string;
  extraction?: string;
  critic?: string;
  editor?: string;
};

export type ResearchSettings = {
  searxng_url: string;
  model_roles?: ResearchModelRoles;
  strong_model?: string;
  fast_model?: string;
  auto_delegate?: boolean;
  effort?: string;
};

export type TradingSettings = {
  execution_mode: "dry_run" | "paper" | "live";
  kill_switch_active: boolean;
  max_order_value_usd: number;
  max_position_pct: number;
  max_open_orders: number;
  max_daily_orders: number;
  max_daily_loss_usd: number;
  max_daily_notional_usd: number;
  max_rebalance_run_notional_usd: number;
  allow_short_selling: boolean;
};

export type ReportsSettings = {
  morning_levels_enabled: boolean;
  hour_et: number;
};

export type AppSettings = {
  primary_broker: "alpaca" | "schwab";
  brokers: {
    alpaca: {
      enabled: boolean;
      api_key_id: string;
      api_secret_key: string;
      api_base_url: string;
      data_base_url: string;
    };
    schwab: {
      enabled: boolean;
      api_key: string;
      app_secret: string;
      callback_url: string;
      account_hash: string;
    };
  };
  ai: AiSettings;
  research?: ResearchSettings;
  trading?: TradingSettings;
  reports?: ReportsSettings;
};

export async function fetchSettings(): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/api/settings`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Settings error ${res.status}`);
  return res.json();
}

export async function saveSettings(
  patch: Partial<AppSettings>,
  adminToken?: string,
): Promise<AppSettings> {
  const res = await apiFetch(
    `${API_BASE}/api/settings`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
    20_000,
    adminToken,
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = (body as { detail?: string }).detail;
    throw new Error(
      detail ??
        (body as { error?: string }).error ??
        `Settings save error ${res.status}`,
    );
  }
  return res.json();
}

export async function setKillSwitch(
  active: boolean,
  adminToken?: string,
): Promise<{ kill_switch_active: boolean }> {
  const res = await apiFetch(
    `${API_BASE}/api/kill-switch`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    },
    20_000,
    adminToken,
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = (body as { detail?: string }).detail;
    throw new Error(
      detail ??
        (body as { error?: string }).error ??
        `Kill switch error ${res.status}`,
    );
  }
  return res.json();
}

export type ChartLayoutItem = {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
  symbol: string;
};

export type Watchlist = {
  id: string;
  name: string;
  symbols: string[];
};

export type WatchlistsState = {
  active_id: string;
  lists: Watchlist[];
};

export type RvolInfo = {
  value: number | null;
  state: "warming" | "live" | "settled" | "overnight";
};

export type WatchlistQuote = {
  price: number;
  change_pct: number;
  sparkline: number[];
  intraday?: number[];
  prev_close?: number;
  volume?: number;
  day_high?: number;
  day_low?: number;
  rvol?: RvolInfo | null;
  market_session?: "pre" | "regular" | "post" | "overnight" | "closed";
  ext_price?: number | null;
  ext_change_pct?: number | null;
  pe?: number | null;
  eps?: number | null;
  div_yield?: number | null;
  market_cap?: number | null;
  last_earnings?: string | null;
  next_earnings?: string | null;
};

export async function fetchWatchlists(): Promise<WatchlistsState> {
  const res = await apiFetch(`${API_BASE}/api/watchlists`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Watchlists error ${res.status}`);
  return res.json();
}

export async function setActiveWatchlist(id: string): Promise<WatchlistsState> {
  const res = await apiFetch(`${API_BASE}/api/watchlists/active`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!res.ok) throw new Error(`Watchlist activate error ${res.status}`);
  return res.json();
}

export async function createWatchlist(
  name: string,
  symbols?: string[],
): Promise<WatchlistsState> {
  const res = await apiFetch(`${API_BASE}/api/watchlists`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, symbols }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Create watchlist error ${res.status}`,
    );
  }
  return res.json();
}

export async function updateWatchlist(
  id: string,
  patch: { name?: string; symbols?: string[] },
): Promise<WatchlistsState> {
  const res = await apiFetch(`${API_BASE}/api/watchlists/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Update watchlist error ${res.status}`,
    );
  }
  return res.json();
}

export async function deleteWatchlist(id: string): Promise<WatchlistsState> {
  const res = await apiFetch(`${API_BASE}/api/watchlists/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Delete watchlist error ${res.status}`,
    );
  }
  return res.json();
}

export async function fetchWatchlistMarket(
  symbols?: string[],
): Promise<Record<string, WatchlistQuote>> {
  const params = symbols?.length
    ? new URLSearchParams({ symbols: symbols.join(",") })
    : new URLSearchParams();
  const res = await apiFetch(`${API_BASE}/api/watchlist/market?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Watchlist market error ${res.status}`,
    );
  }
  const data = await res.json();
  return data.quotes ?? {};
}

export async function fetchWatchlist(): Promise<string[]> {
  const res = await apiFetch(`${API_BASE}/api/watchlist`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Watchlist error ${res.status}`);
  const data = await res.json();
  return data.symbols ?? [];
}

export async function saveWatchlist(symbols: string[]): Promise<string[]> {
  const res = await apiFetch(`${API_BASE}/api/watchlist`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols }),
  });
  if (!res.ok) throw new Error(`Watchlist save error ${res.status}`);
  const data = await res.json();
  return data.symbols ?? [];
}

export async function addWatchlistSymbol(symbol: string): Promise<string[]> {
  const res = await apiFetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
  if (!res.ok) throw new Error(`Watchlist add error ${res.status}`);
  const data = await res.json();
  return data.symbols ?? [];
}

// ── RVOL Scanner ──────────────────────────────────────────────────────────────

export type RvolScannerRow = {
  symbol: string;
  rvol: number;
  total_volume: number;
  price: number;
};

export type RvolScannerResult = {
  as_of: string;
  session: "pre" | "regular" | "post" | "overnight" | "closed";
  count: number;
  results: RvolScannerRow[];
  note?: string;
};

export async function fetchRvolScanner(
  minRvol = 1.5,
  limit = 50,
): Promise<RvolScannerResult> {
  const params = new URLSearchParams({
    min_rvol: String(minRvol),
    limit: String(limit),
  });
  const res = await apiFetch(`${API_BASE}/api/scanner/rvol?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `RVOL scanner error ${res.status}`,
    );
  }
  return res.json();
}

export interface SchwabPosition {
  symbol: string;
  asset_type: string;
  qty: number;
  market_value: number;
  avg_price: number;
  current_price: number;
  unrealized_pl: number;
  day_pl: number;
}

export interface SchwabOpenOrder {
  order_id: string;
  symbol: string;
  side: string;
  qty: number;
  filled_qty: number;
  order_type: string;
  status: string;
  limit_price: number | null;
}

export interface SchwabAccount {
  id: string;
  last4: string;
  label: string;
  account_type: string;
  is_day_trader: boolean;
  liquidation_value: number;
  cash: number;
  long_market_value: number;
  day_pl: number;
  unrealized_pl: number;
  positions: SchwabPosition[];
  open_orders: SchwabOpenOrder[];
}

export interface SchwabAccountsResult {
  authenticated: boolean;
  fetched_at?: number;
  stale?: boolean;
  error?: string;
  message?: string;
  accounts: SchwabAccount[];
  totals: {
    liquidation_value?: number;
    cash?: number;
    long_market_value?: number;
    day_pl?: number;
    unrealized_pl?: number;
    account_count?: number;
  };
}

export async function fetchSchwabAccounts(): Promise<SchwabAccountsResult> {
  const res = await apiFetch(`${API_BASE}/api/schwab/accounts`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ??
        `Schwab accounts error ${res.status}`,
    );
  }
  return res.json();
}

export type SchwabEquityPoint = {
  date: string;
  equity: number;
};

export type SchwabEquityCurve = {
  authenticated: boolean;
  current_liquidation?: number;
  skipped_held_days?: number;
  points: SchwabEquityPoint[];
};

export async function fetchSchwabEquityCurve(
  accountId?: string,
  days = 30,
): Promise<SchwabEquityCurve> {
  const params = new URLSearchParams({ days: String(days) });
  if (accountId) params.set("account_id", accountId);
  const res = await apiFetch(
    `${API_BASE}/api/schwab/equity-curve?${params}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ??
        `Schwab equity curve error ${res.status}`,
    );
  }
  return res.json();
}

export async function removeWatchlistSymbol(symbol: string): Promise<string[]> {
  const res = await apiFetch(
    `${API_BASE}/api/watchlist/${encodeURIComponent(symbol)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`Watchlist remove error ${res.status}`);
  const data = await res.json();
  return data.symbols ?? [];
}

export async function searchSymbols(
  query: string,
  limit = 8,
): Promise<string[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  const res = await apiFetch(`${API_BASE}/api/symbols/search?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Symbol search error ${res.status}`,
    );
  }
  const data = await res.json();
  return data.symbols ?? [];
}

export async function fetchOvernightTradable(
  symbols: string[],
): Promise<string[]> {
  if (symbols.length === 0) return [];
  const params = new URLSearchParams({ symbols: symbols.join(",") });
  const res = await apiFetch(
    `${API_BASE}/api/symbols/overnight?${params}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ??
        `Overnight symbols error ${res.status}`,
    );
  }
  const data = await res.json();
  return data.overnight_tradable ?? [];
}

export async function fetchChartLayout(): Promise<ChartLayoutItem[]> {
  const res = await apiFetch(`${API_BASE}/api/chart-layout`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Chart layout error ${res.status}`);
  const data = await res.json();
  return data.layout ?? [];
}

export async function saveChartLayout(
  layout: ChartLayoutItem[],
): Promise<ChartLayoutItem[]> {
  const res = await apiFetch(`${API_BASE}/api/chart-layout`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layout }),
  });
  if (!res.ok) throw new Error(`Chart layout save error ${res.status}`);
  const data = await res.json();
  return data.layout ?? [];
}

export type PriceAlert = {
  id: number;
  symbol: string;
  price: number;
  direction: string;
  note: string;
  triggered: number;
  repeat: number;
  last_triggered_at?: string | null;
  created_at: string;
};

export type SessionLevelKind =
  | "poc"
  | "support"
  | "resistance"
  | "prior_high"
  | "prior_low";

export type SessionLevel = {
  name: string;
  price: number;
  kind: SessionLevelKind;
};

export type SessionLevelsResponse = {
  symbol: string;
  session_date: string | null;
  levels: SessionLevel[];
};

export async function fetchSessionLevels(
  symbol: string,
): Promise<SessionLevelsResponse> {
  const params = new URLSearchParams({ symbol });
  const res = await apiFetch(`${API_BASE}/api/session-node/levels?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ??
        `Session levels error ${res.status}`,
    );
  }
  return res.json();
}

export type ReportRow = {
  id: string;
  kind: string;
  symbol: string;
  title: string;
  body: string;
  payload: Record<string, unknown>;
  generated_at: string;
};

export async function fetchReports(limit = 30): Promise<ReportRow[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  const res = await apiFetch(`${API_BASE}/api/reports?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Reports error ${res.status}`,
    );
  }
  const data = (await res.json()) as { reports?: ReportRow[] };
  return data.reports ?? [];
}

export async function fetchReport(reportId: string): Promise<ReportRow> {
  const res = await apiFetch(`${API_BASE}/api/reports/${encodeURIComponent(reportId)}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Report error ${res.status}`,
    );
  }
  return res.json();
}

export async function generateReport(
  kind: "morning_levels",
  symbol = "SPY",
  options?: { push?: boolean },
): Promise<ReportRow> {
  const params = options?.push ? "?push=true" : "";
  const res = await apiFetch(`${API_BASE}/api/reports/generate${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, symbol }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Generate report error ${res.status}`,
    );
  }
  return res.json();
}

export async function fetchPriceAlerts(symbol?: string): Promise<PriceAlert[]> {
  const params = symbol
    ? new URLSearchParams({ symbol })
    : new URLSearchParams();
  const res = await apiFetch(`${API_BASE}/api/price-alerts?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Alerts error ${res.status}`);
  const data = await res.json();
  return data.alerts ?? [];
}

export async function createPriceAlert(payload: {
  symbol: string;
  price: number;
  direction?: string;
  note?: string;
  repeat?: boolean;
}): Promise<PriceAlert> {
  const res = await apiFetch(`${API_BASE}/api/price-alerts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Create alert error ${res.status}`);
  return res.json();
}

export async function updatePriceAlert(
  id: number,
  payload: {
    price?: number;
    direction?: string;
    note?: string;
    repeat?: boolean;
  },
): Promise<PriceAlert> {
  const res = await apiFetch(`${API_BASE}/api/price-alerts/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Update alert error ${res.status}`,
    );
  }
  return res.json();
}

export async function deletePriceAlert(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/price-alerts/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete alert error ${res.status}`);
}

export async function triggerPriceAlert(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/price-alerts/${id}/trigger`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Trigger alert error ${res.status}`);
}

export type ChartDrawingKind = "level" | "trendline" | "channel";

export type ChartDrawingData = {
  price?: number;
  points?: { time: number | null; price: number }[];
  width?: number;
  color?: string;
};

export type ChartDrawing = {
  id: number;
  symbol: string;
  kind: ChartDrawingKind;
  data: ChartDrawingData;
  created_at: string;
};

export async function fetchDrawings(symbol?: string): Promise<ChartDrawing[]> {
  const params = symbol
    ? new URLSearchParams({ symbol })
    : new URLSearchParams();
  const res = await apiFetch(`${API_BASE}/api/drawings?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Drawings error ${res.status}`);
  const data = await res.json();
  return data.drawings ?? [];
}

export async function createDrawing(payload: {
  symbol: string;
  kind: ChartDrawingKind;
  data: ChartDrawingData;
}): Promise<ChartDrawing> {
  const res = await apiFetch(`${API_BASE}/api/drawings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Create drawing error ${res.status}`);
  return res.json();
}

export async function updateDrawing(
  id: number,
  payload: { data?: ChartDrawingData; kind?: ChartDrawingKind },
): Promise<ChartDrawing> {
  const res = await apiFetch(`${API_BASE}/api/drawings/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Update drawing error ${res.status}`,
    );
  }
  return res.json();
}

export async function deleteDrawing(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/drawings/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete drawing error ${res.status}`);
}

export type ProposalScoreFactor = {
  name: string;
  direction: "bullish" | "bearish" | string;
  contribution: number;
};

export type ProposalScore = {
  win_prob: number;
  ev_pct: number;
  // generic-scorer fields (optional — absent on strategy-backtest scores)
  ci_low?: number;
  ci_high?: number;
  sufficient?: boolean;
  low_confidence?: boolean;
  base_rate?: number;
  brier?: number;
  n_samples?: number;
  top_factors?: ProposalScoreFactor[];
  // strategy-backtest fields (optional — present when basis === "strategy_backtest")
  basis?: string;
  strategy_id?: string;
  label?: string;
  win_rate?: number;
  ev_ci_pct?: [number, number] | null;
  n_setups?: number;
  per_year_positive?: string | null;
  validated?: string;
  method?: string | null;
  note?: string;
  source?: string;
  as_of?: string;
  plan?: {
    kind?: string;
    exit_basis?: string;
    rules?: string[];
  } | null;
};

export type PendingProposal = {
  id: string;
  summary: string;
  symbol: string;
  side: string;
  qty: number;
  expires_at: string | null;
  payload_hash: string;
  score?: ProposalScore | null;
};

function isProposalScore(value: unknown): value is ProposalScore {
  return (
    typeof value === "object" &&
    value !== null &&
    "win_prob" in value &&
    typeof (value as ProposalScore).win_prob === "number"
  );
}

export async function fetchProposalScore(
  symbol: string,
  side = "buy",
): Promise<ProposalScore | null> {
  const params = new URLSearchParams({ symbol, side });
  const res = await apiFetch(`${API_BASE}/api/proposals/score?${params}`);
  if (!res.ok) return null;
  const data: unknown = await res.json();
  if (
    typeof data === "object" &&
    data !== null &&
    "score" in data &&
    (data as { score: unknown }).score === null
  ) {
    return null;
  }
  if (isProposalScore(data)) return data;
  return null;
}

export async function fetchPendingProposals(): Promise<PendingProposal[]> {
  const res = await apiFetch(`${API_BASE}/api/proposals/pending`);
  if (!res.ok) throw new Error(`pending proposals ${res.status}`);
  const data = await res.json();
  return data.proposals ?? [];
}

export async function approveProposal(
  id: string,
  payloadHash: string,
): Promise<{ status: string; [k: string]: unknown }> {
  const res = await apiFetch(`${API_BASE}/api/proposals/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload_hash: payloadHash }),
  });
  return res.json();
}

export async function rejectProposal(
  id: string,
): Promise<{ status: string; [k: string]: unknown }> {
  const res = await apiFetch(`${API_BASE}/api/proposals/${id}/reject`, {
    method: "POST",
  });
  return res.json();
}

export type EarningsEvent = {
  period: string; // "YYYY-MM-DD"
  label: string; // e.g. "Q2'26"
  eps_actual: number | null;
  eps_estimate: number | null;
  surprise_pct: number | null;
};

export type EarningsHistoryResult = {
  symbol: string;
  events: EarningsEvent[];
};

export async function fetchEarningsHistory(
  symbol: string,
  n = 6,
): Promise<EarningsHistoryResult> {
  const params = new URLSearchParams({ symbol, n: String(n) });
  const res = await apiFetch(
    `${API_BASE}/api/fundamentals/earnings-history?${params}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ??
        `Earnings history error ${res.status}`,
    );
  }
  return res.json();
}

// ── Workspace layout (server-side persistence) ────────────────────────────────

export async function fetchWorkspaceLayout(): Promise<
  WorkspaceLayoutItem[] | null
> {
  try {
    const res = await apiFetch(`${API_BASE}/api/workspace/layout`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data.layout)
      ? (data.layout as WorkspaceLayoutItem[])
      : null;
  } catch {
    return null;
  }
}

export async function saveWorkspaceLayout(
  layout: WorkspaceLayoutItem[],
): Promise<void> {
  try {
    await apiFetch(`${API_BASE}/api/workspace/layout`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout }),
    });
  } catch (err) {
    console.warn("[workspace] server layout save failed:", err);
  }
}

export async function fetchWorkspacePages(): Promise<WorkspacePagesState | null> {
  try {
    const res = await apiFetch(`${API_BASE}/api/workspace/pages`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = await res.json();
    const state = data.pages_state;
    if (
      state &&
      typeof state === "object" &&
      state.pages &&
      typeof state.activePageId === "string"
    ) {
      return state as WorkspacePagesState;
    }
    return null;
  } catch {
    return null;
  }
}

export async function saveWorkspacePages(
  pagesState: WorkspacePagesState,
): Promise<void> {
  try {
    await apiFetch(`${API_BASE}/api/workspace/pages`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pages_state: pagesState }),
    });
  } catch (err) {
    console.warn("[workspace] server pages save failed:", err);
  }
}

// ── Custom themes (server-backed, mirrors workspace pages) ──────────────────────

export async function fetchCustomThemes(): Promise<CustomTheme[] | null> {
  try {
    const res = await apiFetch(`${API_BASE}/api/workspace/custom-themes`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = await res.json();
    const list = data.custom_themes;
    if (Array.isArray(list)) return list as CustomTheme[];
    return null;
  } catch {
    return null;
  }
}

export async function saveCustomThemes(themes: CustomTheme[]): Promise<void> {
  try {
    await apiFetch(`${API_BASE}/api/workspace/custom-themes`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ custom_themes: themes }),
    });
  } catch (err) {
    console.warn("[custom-themes] server save failed:", err);
  }
}

// ── Notifications (server-backed inbox) ─────────────────────────────────────────

export type NotificationSeverity = "info" | "success" | "warning" | "critical";

export type ServerNotification = {
  id: string;
  kind: string;
  title: string;
  body: string;
  severity: NotificationSeverity;
  account_id: string | null;
  strategy_id: string | null;
  ref_type: string | null;
  ref_id: string | null;
  action_url: string | null;
  group_key: string | null;
  data: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
  dismissed_at: string | null;
  read: boolean;
  dismissed: boolean;
};

export type NotificationsResponse = {
  notifications: ServerNotification[];
  unread: number;
};

export async function fetchNotifications(params?: {
  limit?: number;
  before?: string;
  include_dismissed?: boolean;
}): Promise<NotificationsResponse> {
  const search = new URLSearchParams();
  if (params?.limit != null) search.set("limit", String(params.limit));
  if (params?.before) search.set("before", params.before);
  if (params?.include_dismissed) search.set("include_dismissed", "true");
  const qs = search.toString();
  const res = await apiFetch(
    `${API_BASE}/api/notifications${qs ? `?${qs}` : ""}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`Notifications error ${res.status}`);
  return res.json();
}

export async function markNotificationRead(
  id: string,
): Promise<{ notification: ServerNotification }> {
  const res = await apiFetch(
    `${API_BASE}/api/notifications/${encodeURIComponent(id)}/read`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Mark read error ${res.status}`);
  return res.json();
}

export async function markAllNotificationsRead(
  accountId?: string,
): Promise<{ updated: number }> {
  const res = await apiFetch(`${API_BASE}/api/notifications/read-all`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(accountId ? { account_id: accountId } : {}),
  });
  if (!res.ok) throw new Error(`Mark all read error ${res.status}`);
  return res.json();
}

export async function dismissNotification(
  id: string,
): Promise<{ notification: ServerNotification }> {
  const res = await apiFetch(
    `${API_BASE}/api/notifications/${encodeURIComponent(id)}/dismiss`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Dismiss error ${res.status}`);
  return res.json();
}
