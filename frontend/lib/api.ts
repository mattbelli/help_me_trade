import type { AiSettings } from "./ai-settings";
import type { CustomTheme } from "./workspace/custom-themes";
import type {
  WorkspaceLayoutItem,
  WorkspacePagesState,
  WorkspacesState,
} from "./workspace/layout-persistence";
import { isValidWorkspacesState } from "./workspace/layout-persistence";

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
      // Freshness comes from the API's own `Cache-Control: no-store`
      // (api/middleware.py), NOT from a per-fetch cache mode. Chromium skips its
      // CORS preflight cache whenever the request cache mode is no-store, so the
      // `cache: "no-store"` call sites below were costing an extra OPTIONS round
      // trip on EVERY poll — ~49% of all backend requests were preflights
      // (measured 2026-07-25: 6 preflights per 6 fetches vs 1 on the default
      // mode). Pinned here so the chokepoint owns cache policy for every caller.
      cache: "default",
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
  entered_at?: string | null;
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
  // Present in the snapshot since 2026-08-01; the ladder/chart draw them.
  stop_price?: number | null;
  time_in_force?: string | null;
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

export type AlpacaDataFeedInfo = {
  configured: "auto" | "sip" | "iex";
  tier: "paid" | "free";
  feed: "sip" | "iex";
  overnight_feed: "boats" | "overnight";
};

export type BrokerInfo = {
  broker: string;
  primary_broker?: string;
  brokers?: Record<
    string,
    {
      enabled: boolean;
      configured: boolean;
      data_feed?: AlpacaDataFeedInfo;
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

export async function fetchBroker(options?: {
  recheckFeed?: boolean;
}): Promise<BrokerInfo> {
  const params = new URLSearchParams();
  if (options?.recheckFeed) {
    params.set("recheck_feed", "1");
  }
  const query = params.toString();
  const url = query
    ? `${API_BASE}/api/broker?${query}`
    : `${API_BASE}/api/broker`;
  const res = await apiFetch(url, { cache: "no-store" });
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
  /** Older history exists past the first bar returned (backfill on pan-left). */
  has_more?: boolean;
  overnight_delayed?: boolean;
};

export async function fetchPortfolioHistory(
  range: PnlRange,
  accountId?: string,
): Promise<PortfolioHistory> {
  const params = new URLSearchParams({ range });
  // Optional per-account scope: the backend resolves the account's Alpaca
  // adapter; omitting it keeps the original default-broker behavior.
  if (accountId) params.set("account_id", accountId);
  const res = await apiFetch(
    `${API_BASE}/api/portfolio/history?${params}`,
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
  options?: {
    timeframe?: string;
    /** Newest `limit` bars (or the `limit` bars before `before`). */
    limit?: number;
    /** RFC3339 — return bars strictly before this time (an older page). */
    before?: string;
    extended?: boolean;
  },
): Promise<BarsResponse> {
  const params = new URLSearchParams({ symbol });
  if (options?.timeframe) params.set("timeframe", options.timeframe);
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.before) params.set("before", options.before);
  if (options?.extended != null) {
    params.set("extended", options.extended ? "true" : "false");
  }
  const res = await apiFetch(`${API_BASE}/api/bars?${params}`);
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
  // HALT state — blocks exposure-increasing entries while exits still flow.
  // Toggled only via POST /api/entries-halt (rejected by PUT /api/settings),
  // same as kill_switch_active.
  entries_halted: boolean;
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

export type AlertsSettings = {
  // Daily ceiling on flow/confluence phone pushes; in-app cards are never capped.
  max_push_per_day: number;
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
  alerts?: AlertsSettings;
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

export type WatchlistColumnSetting = {
  key: string;
  visible: boolean;
  width?: number;
};

export type WatchlistSettings = {
  columns?: WatchlistColumnSetting[];
  density?: "compact" | "comfortable" | "spacious";
};

export type Watchlist = {
  id: string;
  name: string;
  symbols: string[];
  settings?: WatchlistSettings;
  // Slot-capped lists (the "flow" list): pinned symbols ride above the cap.
  max_slots?: number;
  pinned?: string[];
};

export type WatchlistsState = {
  active_id: string;
  lists: Watchlist[];
};

export type RvolInfo = {
  value: number | null;
  state: "warming" | "live" | "settled" | "overnight";
};

export type AbnormInfo = {
  range_ratio: number | null;
  vol_ratio: number | null;
  rvol_session?: number | null;
  range_pctile?: number | null;
  vol_pctile?: number | null;
  composite?: number | null;
  base_state?: string | null;
  status?: string;
  stale?: boolean;
  label?: string;
  caveat?: string;
};

export type WatchlistQuote = {
  price: number;
  change_pct: number;
  sparkline: number[];
  intraday?: number[];
  prev_close?: number;
  gap_pct?: number | null;
  volume?: number;
  day_high?: number;
  day_low?: number;
  rvol?: RvolInfo | null;
  abnorm?: AbnormInfo | null;
  market_session?: "pre" | "regular" | "post" | "overnight" | "closed";
  ext_price?: number | null;
  ext_change_pct?: number | null;
  overnight_delayed?: boolean;
  pe?: number | null;
  eps?: number | null;
  div_yield?: number | null;
  market_cap?: number | null;
  last_earnings?: string | null;
  next_earnings?: string | null;
  bid?: number | null;
  ask?: number | null;
  // NBBO sizes in SHARES (quote-watch lane; zero until a client watches).
  bid_size?: number | null;
  ask_size?: number | null;
  quote_ts?: number | null;
  w52_high?: number | null;
  w52_low?: number | null;
  atr14?: number | null;
  sma50?: number | null;
  sma200?: number | null;
  // Derived in the widget from spy_change_pct, not sent by the server.
  vs_spy?: number | null;
};

export async function fetchWatchlists(): Promise<WatchlistsState> {
  const res = await apiFetch(`${API_BASE}/api/watchlists`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Watchlists error ${res.status}`);
  return res.json();
}

export type WatchlistAggregate = {
  list_id: string;
  name: string;
  days: number;
  /** Equal-weight cumulative return curve; pct is % from the window start. */
  points: { date: string; pct: number }[];
  return_pct: number | null;
  per_symbol: { symbol: string; return_pct: number }[];
  symbols_missing: string[];
};

export async function fetchWatchlistAggregate(
  listId: string,
  days: number,
): Promise<WatchlistAggregate> {
  const params = new URLSearchParams({ list_id: listId, days: String(days) });
  const res = await apiFetch(`${API_BASE}/api/watchlist/aggregate?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Watchlist aggregate error ${res.status}`);
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
  patch: {
    name?: string;
    symbols?: string[];
    settings?: WatchlistSettings;
    max_slots?: number;
    pinned?: string[];
  },
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
): Promise<{
  quotes: Record<string, WatchlistQuote>;
  spyChangePct: number | null;
}> {
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
  return {
    quotes: data.quotes ?? {},
    spyChangePct: data.spy_change_pct ?? null,
  };
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

export type AbnormalityScannerRow = {
  symbol: string;
  range_ratio: number | null;
  vol_ratio: number | null;
  rvol_session?: number | null;
  range_pctile?: number | null;
  vol_pctile?: number | null;
  composite?: number | null;
  base_state?: string;
  status?: string;
  caveat?: string;
};

export type AbnormalityScannerResult = {
  as_of: string;
  count: number;
  fires_today?: number;
  results: AbnormalityScannerRow[];
  stats?: {
    n: number;
    median_excess: number | null;
    direction?: {
      up: number;
      down: number;
      up_rate: number | null;
      up_wilson_95: [number, number] | null;
    } | null;
  };
  label?: string;
  caveat?: string;
};

export async function fetchAbnormalityScanner(opts?: {
  fired?: boolean;
  paired?: boolean;
  base_low?: boolean;
  base_high?: boolean;
  limit?: number;
}): Promise<AbnormalityScannerResult> {
  const params = new URLSearchParams({ limit: String(opts?.limit ?? 50) });
  if (opts?.fired) params.set("fired", "true");
  if (opts?.paired) params.set("paired", "true");
  if (opts?.base_low) params.set("base_low", "true");
  if (opts?.base_high) params.set("base_high", "true");
  const res = await apiFetch(`${API_BASE}/api/scanner/abnormality?${params}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Abnormality scanner error ${res.status}`,
    );
  }
  return res.json();
}

export type OptionsFlowEvent = {
  id: string;
  ts: string;
  underlying: string;
  occ_symbol: string | null;
  right: string | null;
  strike: number | null;
  expiry: string | null;
  dte: number | null;
  otm_pct: number | null;
  event_type: string;
  contracts: number | null;
  premium_usd: number | null;
  vol_oi_ratio: number | null;
  vol_zscore: number | null;
  direction: "buy_side" | "sell_side" | "undetermined";
  direction_basis: string;
  /** Corpus midpoint rule beside DIR-1 — annotation, not a confirmed aggressor. */
  mid_side?: "buy" | "sell" | "unclassified" | "wide_spread" | null;
  spread_flag: boolean | null;
  n_prints: number | null;
  exchanges: string[];
  reason: string;
  caveat: string;
  detail?: Record<string, number> | null;
  /** Laplace rank vs that name's own cell; smaller = rarer. Null = unranked. */
  rarity_p?: number | null;
  rarity_n?: number | null;
  rarity_sentence?: string | null;
  /** rarity × tenor × OTM × type, 0-10 display. Perception, not a forecast. */
  conviction?: number | null;
  conviction_parts?: {
    rarity: number;
    dte: number;
    otm: number;
    type: number;
    ranked: boolean;
  } | null;
};

export type OptionsFlowResult = {
  as_of: string;
  count: number;
  events: OptionsFlowEvent[];
};

export type IndexPressureRow = {
  underlying: string;
  bull_usd: number;
  bear_usd: number;
  unclassified_usd: number;
  net_usd: number;
  classified_frac: number;
  n_prints: number;
  minutes_with_data: number;
  last_minute: number;
  coverage: string | null;
};

export type IndexPressureResult = {
  as_of: string;
  date: string;
  /** True when today had no window data and the newest prior session is served. */
  fallback: boolean;
  window: "open" | "close";
  window_et: string;
  rows: IndexPressureRow[];
  baseline_days: number;
};

export type IndexPressureTrendWindow = {
  bull_usd: number;
  bear_usd: number;
  unclassified_usd: number;
  net_usd: number;
  classified_frac: number;
  n_prints: number;
};

export type IndexPressureTrendDay = {
  date: string;
  open: IndexPressureTrendWindow | null;
  close: IndexPressureTrendWindow | null;
  spy_pct: number | null;
};

export type IndexPressureTrendResult = {
  as_of: string;
  days: IndexPressureTrendDay[];
};

export type OptionsChainSide = {
  bid: number | null;
  ask: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  volume: number | null;
  oi: number | null;
  last: number | null;
};

export type OptionsChainRow = {
  strike: number;
  call: OptionsChainSide;
  put: OptionsChainSide;
};

export type OptionsChainResult = {
  symbol: string;
  underlying_price: number | null;
  expirations: string[];
  expiry: string | null;
  rows: OptionsChainRow[];
  as_of: string | null;
  // Who produced the iv/greek columns. Alpaca publishes them on every expiry
  // except the one expiring today, so a 0DTE board is entirely ours and a wide
  // strike window is usually both — the user should never have to guess which.
  greeks_source?: "vendor" | "black76" | "black76_spot" | "mixed";
};

export type OptionsChainStrikes = 6 | 10 | 20 | 40 | "all";

// OPRA option chain snapshot for one underlying + expiration (read-only).
export async function fetchOptionsChain(
  symbol: string,
  expiry?: string,
  strikes: OptionsChainStrikes | number = 10,
): Promise<OptionsChainResult> {
  const params = new URLSearchParams({
    symbol: symbol.trim().toUpperCase(),
    strikes: String(strikes),
  });
  if (expiry) {
    params.set("expiry", expiry.trim().slice(0, 10));
  }
  const res = await apiFetch(`${API_BASE}/api/options-chain?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Options-chain error ${res.status}`,
    );
  }
  return res.json();
}

// Unusual options activity (Mode-A perception — facts about the tape, not a signal).
export async function fetchOptionsFlow(
  limit = 200,
  symbol?: string,
  eventType?: string,
): Promise<OptionsFlowResult> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (symbol) {
    params.set("symbol", symbol.trim().toUpperCase());
  }
  if (eventType) {
    params.set("event_type", eventType);
  }
  const res = await apiFetch(`${API_BASE}/api/options-flow?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Options-flow error ${res.status}`,
    );
  }
  return res.json();
}

export async function fetchIndexPressure(
  window?: "open" | "close",
  date?: string,
): Promise<IndexPressureResult> {
  const params = new URLSearchParams();
  if (window) params.set("window", window);
  if (date) params.set("date", date);
  const qs = params.size > 0 ? `?${params}` : "";
  const res = await apiFetch(`${API_BASE}/api/index-pressure${qs}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Index-pressure error ${res.status}`,
    );
  }
  return res.json();
}

export async function fetchIndexPressureTrend(
  days = 40,
): Promise<IndexPressureTrendResult> {
  const res = await apiFetch(`${API_BASE}/api/index-pressure/history?days=${days}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Pressure-trend error ${res.status}`,
    );
  }
  return res.json();
}

export interface OvernightFlowTopPrint {
  sym: string;
  size: number;
  price: number;
  notional: number;
  cond: string;
  et: string;
}

export interface OvernightFlowLine {
  session: string;
  /** Null when synthesized from history (no live line written yet). */
  generated_at: string | null;
  window_et: string;
  slice: {
    prints: number;
    notional: number;
    put_share_notional: number;
    zero_dte_share_notional: number;
    macro_window_share: number;
    top_prints?: OvernightFlowTopPrint[];
    full_night?: { prints: number; notional: number; evening_notional: number };
  };
  baseline: {
    nights: number;
    z: number | null;
    median: number | null;
    sigma: number | null;
    note?: string;
  };
  caveats: string[];
}

export interface OvernightFlowResult {
  line: OvernightFlowLine | null;
  history: { session: string; notional: number; put_share_notional: number }[];
}

export async function fetchOvernightFlow(): Promise<OvernightFlowResult> {
  const res = await apiFetch(`${API_BASE}/api/overnight-flow`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Overnight-flow error ${res.status}`,
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
  | "prior_low"
  | "prior_week_high"
  | "prior_week_low";

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
  limit_price?: number | null;
  notional?: number | null;
  expires_at: string | null;
  payload_hash: string;
  score?: ProposalScore | null;
  /** A book's order-management verb (move_stop / cancel_entry); null for a trade. */
  order_action?: string | null;
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

/** Desk approvals ride the ADMIN token (Settings → Execution / Risk stores it
 *  for the session). Once a phone is enrolled the shared API token can no
 *  longer approve — the server answers 403 and we surface that as `forbidden`. */
export async function approveProposal(
  id: string,
  payloadHash: string,
): Promise<{ status: string; [k: string]: unknown }> {
  const adminToken =
    typeof window !== "undefined"
      ? (sessionStorage.getItem("hmt-admin-token") ?? "")
      : "";
  const res = await apiFetch(
    `${API_BASE}/api/proposals/${id}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload_hash: payloadHash }),
    },
    20_000,
    adminToken,
  );
  if (res.status === 403) return { status: "forbidden" };
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

export async function fetchWorkspaces(): Promise<WorkspacesState | null> {
  try {
    const res = await apiFetch(`${API_BASE}/api/workspace/workspaces`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = await res.json();
    const state: unknown = data.workspaces_state;
    return isValidWorkspacesState(state) ? state : null;
  } catch {
    return null;
  }
}

export async function saveWorkspaces(state: WorkspacesState): Promise<void> {
  try {
    await apiFetch(`${API_BASE}/api/workspace/workspaces`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspaces_state: state }),
    });
  } catch (err) {
    console.warn("[workspace] server workspaces save failed:", err);
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

export type BoardCardType =
  | "feature"
  | "bug"
  | "safety"
  | "research"
  | "ops"
  | "idea";
export type BoardCardPriority = "high" | "normal" | "low";

export type BoardCard = {
  num?: number;
  id: string;
  title: string;
  lane: string;
  type: BoardCardType;
  priority: BoardCardPriority;
  notes: string;
  owner: string;
  updated_at: string;
};

export type BoardData = {
  updated_at: string | null;
  lanes: string[];
  cards: BoardCard[];
};

export async function fetchBoard(): Promise<BoardData> {
  const res = await apiFetch(`${API_BASE}/api/board`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Board error ${res.status}`);
  const data = await res.json();
  return data.board as BoardData;
}

export type BoardInboxAddResponse = {
  ok: true;
  card: BoardCard;
};

export async function addBoardInboxCard(
  title: string,
  notes?: string,
): Promise<BoardInboxAddResponse> {
  const res = await apiFetch(`${API_BASE}/api/board/inbox`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, notes: notes ?? "" }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : `Board inbox error ${res.status}`;
    throw new Error(detail);
  }
  return res.json() as Promise<BoardInboxAddResponse>;
}

export type IdeaOutcomeStatus =
  | "pending"
  | "triggered"
  | "won"
  | "lost"
  | "expired"
  | "invalidated"
  | "data_gap";

export type IdeaOutcomeRow = {
  id: number;
  artifact_id: number;
  idea_index: number;
  symbol: string;
  as_of: string;
  entry_low: string;
  entry_high: string;
  stop: string;
  targets: string[];
  status: IdeaOutcomeStatus;
  origin: string;
  triggered_at: string | null;
  resolved_at: string | null;
  resolution_price: string | null;
  first_target_hit: number | null;
  mfe_pct: number | null;
  mae_pct: number | null;
  founder_verdict: string | null;
  verdict: IdeaVerdict | null;
  notes: string | null;
  updated_at: string;
  // Open-row context (stamped by the tracker sweep / API): why still pending.
  last_price?: string | null;
  last_price_at?: string | null;
  expires_on?: string | null;
  zone_distance_pct?: number | null;
};

export type IdeaVerdict = {
  take_it: number;
  entry_honest: number;
  stop_survivable: number;
  thesis_quality: number;
  note?: string;
  graded_at?: string;
};

export type IdeaGradingSummary = {
  graded: number;
  means: Record<string, number | null>;
  mean_take_it: number | null;
  numbers_wrong: number;
  gate: {
    min_graded: number;
    min_mean_take_it: number;
    max_numbers_wrong: number;
  };
  gate_pass: boolean;
};

export type IdeaOutcomesSummary = {
  counts: Record<string, number>;
  resolved: number;
  win_rate: number | null;
  grading?: IdeaGradingSummary;
  by_origin?: Record<
    string,
    {
      counts: Record<string, number>;
      resolved: number;
      win_rate: number | null;
    }
  >;
};

export type IdeaOutcomesResponse = {
  summary: IdeaOutcomesSummary;
  open: IdeaOutcomeRow[];
  resolved: IdeaOutcomeRow[];
};

export async function getIdeaOutcomes(): Promise<IdeaOutcomesResponse> {
  const res = await apiFetch(`${API_BASE}/api/ideas/outcomes`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Idea outcomes error ${res.status}`);
  return res.json();
}

export type IdeaLedgerRow = {
  id: number;
  as_of: string;
  updated_at: string;
  symbol: string;
  direction: string;
  origin: string;
  idea_index: number;
  entry_low: string;
  entry_high: string;
  stop: string;
  targets_json: string;
  status: IdeaOutcomeStatus;
  user_action: string;
  ret_pct: number | null;
  spy_ret_pct: number | null;
  alpha_pct: number | null;
  source_ref: string | null;
};

export type IdeaLedgerResponse = {
  rows: IdeaLedgerRow[];
  total: number;
};

export async function getIdeaLedger(params?: {
  origin?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<IdeaLedgerResponse> {
  const query = new URLSearchParams();
  if (params?.origin) query.set("origin", params.origin);
  if (params?.status) query.set("status", params.status);
  if (params?.limit != null) query.set("limit", String(params.limit));
  if (params?.offset != null) query.set("offset", String(params.offset));
  const suffix = query.size > 0 ? `?${query}` : "";
  const res = await apiFetch(`${API_BASE}/api/ideas/ledger${suffix}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Idea ledger error ${res.status}`);
  return res.json();
}

export type FusionEpisode = {
  id: string;
  symbol: string;
  market_day: string;
  opened_at: string;
  updated_at: string;
  anchor_dot_id: string | null;
  anchor_price: string | null;
  anchor_atr14: string | null;
  anchor_direction: string | null;
  registration_called: number;
  score: number;
  direction_state: string;
  contested: number;
  signature: string;
  market_shock: number;
  dot_count?: number;
};

export type EvidenceDot = {
  id: string;
  episode_id: string;
  source_store: string;
  source_event_id: string;
  source_ingest_seq: number;
  symbol: string;
  ts: string;
  component: string;
  actor_group: string;
  dot_kind: string;
  direction: string;
  raw_value: string | null;
  points: number;
  detail: Record<string, unknown>;
  trigger_price: string | null;
  prior_close_atr14: string | null;
  stamped: number;
  created_at: string;
};

export type FusionEpisodesResponse = {
  episodes: FusionEpisode[];
  day: string | null;
};

export type FusionEpisodeResponse = {
  episode: FusionEpisode;
  dots: EvidenceDot[];
};

export async function getFusionEpisodes(
  day?: string,
): Promise<FusionEpisodesResponse> {
  const query = day ? `?${new URLSearchParams({ day })}` : "";
  const res = await apiFetch(`${API_BASE}/api/fusion/episodes${query}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Fusion episodes error ${res.status}`);
  return res.json();
}

export async function getFusionEpisode(
  id: string,
): Promise<FusionEpisodeResponse> {
  const res = await apiFetch(
    `${API_BASE}/api/fusion/episodes/${encodeURIComponent(id)}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`Fusion episode error ${res.status}`);
  return res.json();
}

export type FusionDossierClaim = {
  claim_id: string;
  angle: string;
  statement: string;
  value: string | number | null;
  ts: string;
  direction: string;
  fresh: boolean;
  contradicts: string[];
};

export type FusionDossier = {
  episode_id: string;
  symbol: string;
  market_day: string;
  claims: FusionDossierClaim[];
  summary: string;
};

export async function getFusionEpisodeDossier(
  id: string,
): Promise<FusionDossier> {
  const res = await apiFetch(
    `${API_BASE}/api/fusion/episodes/${encodeURIComponent(id)}/dossier`,
  );
  if (!res.ok) throw new Error(`Fusion dossier error ${res.status}`);
  const body = (await res.json()) as { dossier: FusionDossier };
  return body.dossier;
}

export type FusionRadarRow = {
  symbol: string;
  tier: "live" | "poll" | "scan";
  maturity: number;
  thesis: {
    bull_mass: number;
    bear_mass: number;
    net: number;
    conviction: number;
    z: number;
    contested: boolean;
  };
  trigger: {
    bull_mass: number;
    bear_mass: number;
  };
  last_evidence_at: string | null;
  dot_count: number;
};

export type FusionRadarResponse = {
  rows: FusionRadarRow[];
  now: string;
  sessions: number;
  /** Server has FUSION_ENABLED=0 — the layer is gated off, not merely quiet. */
  disabled?: boolean;
};

export async function getFusionRadar(
  params?: { sessions?: number },
): Promise<FusionRadarResponse> {
  const query =
    params?.sessions != null
      ? `?${new URLSearchParams({ sessions: String(params.sessions) })}`
      : "";
  const res = await apiFetch(`${API_BASE}/api/fusion/radar${query}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Fusion radar error ${res.status}`);
  return res.json();
}

export type FilingKind = "insider_buy" | "stake_13d" | "congress_trade";

export type FilingEvent = {
  id: number;
  uid: string;
  kind: FilingKind;
  symbol: string;
  person: string;
  role: string;
  side: "buy" | "sell" | "";
  value_usd: number | null;
  value_label: string;
  filed_at: string;
  traded_at: string | null;
  url: string;
  detail: Record<string, unknown>;
};

export async function fetchFilings(
  kinds?: FilingKind[],
  limit = 150,
): Promise<FilingEvent[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (kinds && kinds.length > 0) params.set("kinds", kinds.join(","));
  const res = await apiFetch(`${API_BASE}/api/filings?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Filings error ${res.status}`);
  const data = await res.json();
  return data.events ?? [];
}

export type IdeaGradeInput = {
  take_it: number;
  entry_honest: number;
  stop_survivable: number;
  thesis_quality: number;
  note?: string;
};

export async function gradeIdeaOutcome(
  outcomeId: number,
  grade: IdeaGradeInput,
): Promise<IdeaVerdict> {
  const res = await apiFetch(
    `${API_BASE}/api/ideas/outcomes/${outcomeId}/grade`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(grade),
    },
  );
  if (!res.ok) {
    let detail = `Grade error ${res.status}`;
    try {
      const data = await res.json();
      detail = data.error ?? data.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const data = await res.json();
  return data.verdict as IdeaVerdict;
}

// ── Strategy Lab (read-only research artifacts) ──────────────────────────────

export type StrategyLabPoint = [date: string, equity: number];

export type StrategyLabMetrics = {
  cagr?: number;
  max_drawdown?: number;
  sharpe?: number;
  avg_invested?: number;
  per_trade_mean_pct?: number;
  win_pct?: number;
};

export type StrategyLabSleeve = {
  id: string;
  label: string;
  generated: string;
  config: Record<string, unknown> & { n_trades?: number };
  provenance?: Record<string, unknown>;
  caveats: string[];
  metrics: StrategyLabMetrics;
  equity: StrategyLabPoint[];
};

export type StrategyLabCatalog = {
  sleeves: Omit<StrategyLabSleeve, "equity">[];
  benchmarks: string[];
  capability?: string;
  error?: string;
};

export type StrategyLabBenchmarks = {
  generated: string;
  series: Record<string, StrategyLabPoint[]>;
};

export type StrategyLabCohortCatalog = {
  generated_at: string | null;
  capability: string;
  lanes: Array<Record<string, unknown>>;
};

export type StrategyLabPaperPosition = {
  symbol: string;
  qty: string | null;
  entry_date: string;
  entry_price: string | null;
  mark: number | null;
  unrealized_pct: number | null;
  days_held: number | null;
};

export type StrategyLabPaperBook = {
  strategy_id: string;
  label: string;
  positions: StrategyLabPaperPosition[];
  n_positions: number;
  mean_unrealized_pct: number | null;
  oldest_entry: string | null;
  planned_exit_note: string | null;
};

export type StrategyLabPaperCatalog = {
  books: StrategyLabPaperBook[];
};

async function strategyLabJson<T>(path: string): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Strategy Lab API error ${res.status}`);
  return res.json() as Promise<T>;
}

export function fetchStrategyLabCatalog(): Promise<StrategyLabCatalog> {
  return strategyLabJson("/api/strategy-lab");
}

export function fetchStrategyLabBenchmarks(): Promise<StrategyLabBenchmarks> {
  return strategyLabJson("/api/strategy-lab/benchmarks");
}

export function fetchStrategyLabSleeve(id: string): Promise<StrategyLabSleeve> {
  return strategyLabJson(
    `/api/strategy-lab/sleeves/${encodeURIComponent(id)}`,
  );
}

export function fetchStrategyLabCohorts(): Promise<StrategyLabCohortCatalog> {
  return strategyLabJson("/api/strategy-lab/cohorts");
}

export function fetchStrategyLabPaper(): Promise<StrategyLabPaperCatalog> {
  return strategyLabJson("/api/strategy-lab/paper");
}

// ── Strategy Lab (workspace built-in view) ─────────────────────────────────
// Typed fetchers for the Lab cockpit. All read-only; every number rendered in
// the Lab is one of these live responses or an explicit honest empty state.

export type LabStrategy = {
  id: string;
  name: string;
  enabled: boolean;
  account_id: string | null;
  account_label: string | null;
  notional_usd: number;
  notional_pct: number | null;
  exit_mode: string;
  entry_mode: string; // "auto" | "propose"
  universe: string;
  max_concurrent_positions: number | null;
  max_deployed_usd: number | null;
  params: Record<string, unknown>;
};

export async function fetchLabStrategies(): Promise<LabStrategy[]> {
  const res = await apiFetch(`${API_BASE}/api/strategies`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Strategies error ${res.status}`);
  const body = (await res.json()) as { strategies?: LabStrategy[] };
  return body.strategies ?? [];
}

export type LabAccount = {
  id: string;
  label: string;
  broker: string;
  is_default: boolean;
};

export async function fetchLabAccounts(): Promise<LabAccount[]> {
  const res = await apiFetch(`${API_BASE}/api/strategies/accounts`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Accounts error ${res.status}`);
  const body = (await res.json()) as { accounts?: LabAccount[] };
  return body.accounts ?? [];
}

export type LabDayPnl = {
  day_pnl: number;
  has_activity: boolean;
  scope_fallback?: boolean | null;
};

export async function fetchLabDayPnl(accountId?: string): Promise<LabDayPnl> {
  const params = new URLSearchParams();
  if (accountId) params.set("account_id", accountId);
  const qs = params.toString();
  const res = await apiFetch(
    `${API_BASE}/api/performance/day-pnl${qs ? `?${qs}` : ""}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`Day P&L error ${res.status}`);
  return res.json();
}

export type LabExecution = {
  id: string;
  symbol: string;
  side: string;
  qty: number;
  order_type: string;
  limit_price: number | null;
  status: string;
  filled_qty: number | null;
  avg_fill_price: number | null;
  execution_mode: string;
  submitted_at: string | null;
  created_at: string;
};

export async function fetchLabExecutions(
  limit = 100,
): Promise<LabExecution[]> {
  const res = await apiFetch(
    `${API_BASE}/api/orders/executions?limit=${limit}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`Executions error ${res.status}`);
  const body = (await res.json()) as { executions?: LabExecution[] };
  // qty/prices arrive as Decimal-serialized strings (trading stores money/qty
  // as TEXT) — normalize to numbers so rendering/formatting is sane.
  const num = (v: unknown): number | null =>
    v == null || v === "" ? null : Number(v);
  return (body.executions ?? []).map((e) => ({
    ...e,
    qty: num(e.qty) ?? 0,
    filled_qty: num(e.filled_qty),
    avg_fill_price: num(e.avg_fill_price),
    limit_price: num(e.limit_price),
  }));
}

export type LabPendingProposal = {
  id: string;
  summary: string;
  symbol: string;
  side: string;
  qty: number;
  expires_at: string | null;
  payload_hash: string | null;
  score: Record<string, unknown> | null;
};

export async function fetchLabPendingProposals(): Promise<LabPendingProposal[]> {
  const res = await apiFetch(`${API_BASE}/api/proposals/pending`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Proposals error ${res.status}`);
  const body = (await res.json()) as { proposals?: LabPendingProposal[] };
  return body.proposals ?? [];
}

export type LabIdeaRow = {
  id: number;
  as_of: string;
  updated_at: string;
  symbol: string;
  direction: string; // "long" | "short"
  origin: string;
  status: string;
  user_action: string;
  entry_low: number;
  entry_high: number;
  stop: number;
  ret_pct: number | null;
  spy_ret_pct: number | null;
  alpha_pct: number | null;
};

export async function fetchLabIdeaLedger(
  origin: string,
  limit = 50,
): Promise<LabIdeaRow[]> {
  const params = new URLSearchParams({ origin, limit: String(limit) });
  const res = await apiFetch(`${API_BASE}/api/ideas/ledger?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Idea ledger error ${res.status}`);
  const body = (await res.json()) as { rows?: LabIdeaRow[]; total?: number };
  return body.rows ?? [];
}

export type LabJobHealth = {
  name: string;
  interval_s: number;
  last_success_at: string | null;
  lag_s: number | null;
  consecutive_failures: number;
  last_error: string | null;
  enabled: boolean;
};

export async function fetchLabJobs(): Promise<LabJobHealth[]> {
  const res = await apiFetch(`${API_BASE}/health`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Health error ${res.status}`);
  const body = (await res.json()) as { jobs?: LabJobHealth[] };
  return body.jobs ?? [];
}

// --- collector stats (the toolbar feed panels) ---------------------------------------------

export type CollectorLag = {
  n: number;
  p50: number | null;
  p90: number | null;
  p95: number | null;
  p99: number | null;
  max: number | null;
};

/** One Rust collector's 1 s snapshot (`data/collector_stats/<name>.json`). */
export type CollectorSnapshot = {
  name: "equity" | "flow";
  ts: number;
  age_s: number | null;
  stale: boolean;
  pid: number;
  uptime_s: number;
  connected: boolean;
  rate_1s: number;
  rate_60s: number;
  lag_ms: { now: CollectorLag; day: CollectorLag };
  /** [epoch_min, msgs/s, lag p95 ms|null] per closed minute of the ET day */
  minutes: [number, number, number | null][];
  reconnects: number;
  rss_mb: number;
  // equity
  msgs_total?: number;
  by_type?: Record<string, number>;
  events?: {
    persisted: number;
    sweeps: number;
    velocity: number;
    spots: number;
    bars_rows: number;
  };
  proxy?: {
    clients: number;
    subs_trades: number;
    subs_quotes: number;
    subs_daily: number;
    drops: number;
  };
  recorder?: { frames: number; drops: number } | null;
  vendor_errors?: number;
  // flow
  feed_state?: string;
  stalled_s?: number;
  prints_total?: number;
  data_msgs?: number;
  prints_per_min?: number;
  mapped?: number;
  acks?: number;
  slow?: number;
  dropped?: number;
  unresolved_syms?: number;
  no_meta?: number;
  gap_s?: number;
  flow_events?: number;
  minute_rows?: number;
  coded?: number;
  repairs?: number;
  pairing?: {
    dbn_seen: number;
    alp_seen: number;
    matched: number;
    resolved: number;
    matched_pct: number;
    enriched: number;
    repairs: number;
    tolerance: number;
    expired: number;
    evicted: number;
    late_twin: number;
    orphans: number;
    enrich_buckets: {
      le10ms: number;
      le100ms: number;
      le1s: number;
      le10s: number;
      gt10s: number;
    };
    pending_dbn: number;
    pending_alpaca: number;
    keys_peak: number;
    acked_wide: boolean;
    resets: number;
    reconnects: number;
    gth_written: number;
    gth_dropped: number;
  } | null;
};

export type CollectorStats = {
  equity: CollectorSnapshot | null;
  flow: CollectorSnapshot | null;
};

export async function fetchCollectorStats(): Promise<CollectorStats> {
  const res = await apiFetch(`${API_BASE}/api/collectors/stats`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
