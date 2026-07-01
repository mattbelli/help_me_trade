import { API_BASE, apiFetch } from "@/lib/api";

// --- AI side: the agent's own decision journal -------------------------------

export type JournalDecision = {
  proposal_id: string | null;
  symbol: string | null;
  side: string | null;
  strategy_id: string | null;
  source: string | null;
  thesis: string | null;
  win_prob: number | null;
  ev_pct: number | null;
  decision: string | null;
  outcome: string | null;
  realized_pnl: string | null;
  r_multiple: number | null;
  holding_days: number | null;
  created_at: string | null;
};

export type StrategyStats = {
  strategy_id: string;
  n: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  net_realized_pnl: string | null;
  avg_r_multiple: number | null;
};

export type DecisionStats = {
  overall: {
    n: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    avg_r_multiple: number | null;
    net_realized_pnl: string | null;
    note: string | null;
  };
  by_strategy: StrategyStats[];
};

export type JournalDecisionsResult = {
  count: number;
  decisions: JournalDecision[];
  stats: DecisionStats;
};

// --- Me side: the user's real broker trades ----------------------------------

export type MyTrade = {
  symbol: string | null;
  asset_class: string | null;
  side: string;
  quantity: string | null;
  open_date: string | null;
  close_date: string | null;
  hold_days: number | null;
  pnl: string | null;
  outcome: string | null;
  pnl_basis: string;
};

export type MyTradesResult = {
  count: number;
  trades: MyTrade[];
};

export type MyTradesSummary = {
  net_cash_realized: {
    realized_closed: string | null;
    closed_win_rate: number | null;
    reliability: Record<string, string>;
  };
  behavioral: {
    trade_count: number;
    date_range: { from: string; to: string } | null;
    asset_mix: {
      equity_trades: number;
      option_trades: number;
      equity_pct: number | null;
      option_pct: number | null;
    };
    top_symbols: { symbol: string; value: string | number; share_pct: number | null }[];
    same_day_flips: number;
    buy_sell_ratio: number | null;
  };
  honesty: string;
};

async function getJson<T>(url: string, errLabel: string): Promise<T> {
  const res = await apiFetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `${errLabel} error ${res.status}`,
    );
  }
  return res.json() as Promise<T>;
}

export async function fetchDecisions(
  symbol?: string,
  limit = 30,
): Promise<JournalDecisionsResult> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (symbol) params.set("symbol", symbol);
  return getJson(
    `${API_BASE}/api/journal/decisions?${params}`,
    "Journal decisions",
  );
}

export async function fetchMyTrades(
  symbol?: string,
  limit = 30,
): Promise<MyTradesResult> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (symbol) params.set("symbol", symbol);
  return getJson(`${API_BASE}/api/journal/trades?${params}`, "Journal trades");
}

export async function fetchMyTradesSummary(
  symbol?: string,
): Promise<MyTradesSummary> {
  const params = new URLSearchParams();
  if (symbol) params.set("symbol", symbol);
  const qs = params.toString();
  return getJson(
    `${API_BASE}/api/journal/trades/summary${qs ? `?${qs}` : ""}`,
    "Journal trade summary",
  );
}
