import { API_BASE, apiFetch } from "@/lib/api";

// Company profile (Finnhub /stock/profile2, trimmed server-side). Best-effort:
// every field is optional — a missing Finnhub key returns {}.
export type TickerProfile = {
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
};

// Curated fundamentals (Finnhub /stock/metric, flattened server-side).
export type TickerMetrics = {
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
};

export type TickerNewsItem = {
  headline: string;
  source: string;
  url: string;
  datetime: string | null; // ISO 8601
  summary: string;
  image: string;
};

// The app's own per-symbol context (ai.perception.context.build_symbol_context).
export type TickerFacts = {
  symbol?: string;
  quote?: {
    price?: number;
    change_pct?: number;
    market_session?: string;
    ext_price?: number | null;
    ext_change_pct?: number | null;
  };
  rvol?: { value: number | null; state?: string };
  session_nodes?: {
    poc?: number | null;
    vah?: number | null;
    val?: number | null;
    pdh?: number | null;
    pdl?: number | null;
    session_date?: string | null;
  };
  technicals?: {
    sma50?: number;
    sma200?: number;
    vs_sma200_pct?: number;
    rsi14?: number;
    atr14?: number;
    dist_from_20d_high_pct?: number;
    trend?: string;
  };
  fundamentals?: {
    pe?: number;
    eps?: number;
    div_yield?: number;
    market_cap?: number;
  };
  earnings?: { last?: string; next?: string };
  decision_score?: {
    win_prob?: number | null;
    ev?: number | null;
    top_factors?: unknown;
    ci?: [number | null, number | null];
  } | null;
  active_alerts?: Array<{
    id?: number;
    price?: number;
    direction?: string;
    repeat?: boolean;
    note?: string;
  }>;
};

export type TickerInfo = {
  symbol: string;
  profile: TickerProfile;
  metrics: TickerMetrics;
  news: TickerNewsItem[];
  facts: TickerFacts;
};

export async function fetchTickerInfo(symbol: string): Promise<TickerInfo> {
  const res = await apiFetch(
    `${API_BASE}/api/ticker/${encodeURIComponent(symbol)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Ticker info error ${res.status}`,
    );
  }
  return res.json();
}

// News aggregated across the union of symbols in ALL watchlists.
export type WatchlistNewsItem = TickerNewsItem & {
  symbol: string;
};

export async function fetchWatchlistNews(
  limit = 50,
): Promise<WatchlistNewsItem[]> {
  const res = await apiFetch(
    `${API_BASE}/api/ticker/watchlist/news?limit=${encodeURIComponent(limit)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Watchlist news error ${res.status}`,
    );
  }
  const data = await res.json();
  return data.news ?? [];
}
