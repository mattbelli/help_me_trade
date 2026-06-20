import type { AiSettings } from "./ai-settings";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";

export function apiAuthHeaders(extra?: HeadersInit): HeadersInit {
  const headers = new Headers(extra);
  if (API_TOKEN) {
    headers.set("X-API-Token", API_TOKEN);
  }
  return headers;
}

export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs = 20_000,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, {
      ...init,
      signal: init?.signal ?? controller.signal,
      headers: apiAuthHeaders(init?.headers),
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

export type Snapshot = {
  captured_at: string;
  broker?: string;
  primary_broker?: string;
  account: Account;
  position_count: number;
  positions: Position[];
  open_order_count: number;
  open_orders: Order[];
  snapshots?: BrokerSnapshot[];
};

export type BrokerSnapshot = {
  broker: string;
  captured_at: string;
  account: Account;
  position_count: number;
  positions: Position[];
  open_order_count: number;
  open_orders: Order[];
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

export type TradingSettings = {
  execution_mode: "dry_run" | "paper" | "live";
  kill_switch_active: boolean;
  max_order_value_usd: number;
  max_position_pct: number;
  max_open_orders: number;
  max_daily_orders: number;
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
  research?: {
    searxng_url: string;
  };
  trading?: TradingSettings;
};

export async function fetchSettings(): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/api/settings`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Settings error ${res.status}`);
  return res.json();
}

export async function saveSettings(patch: AppSettings): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/api/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Settings save error ${res.status}`,
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

export type WatchlistQuote = {
  price: number;
  change_pct: number;
  sparkline: number[];
  prev_close?: number;
  volume?: number;
  day_high?: number;
  day_low?: number;
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
  created_at: string;
};

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
}): Promise<PriceAlert> {
  const res = await apiFetch(`${API_BASE}/api/price-alerts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Create alert error ${res.status}`);
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
