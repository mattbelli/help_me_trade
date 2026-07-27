import { APP_VERSION } from '@/config';

import { apiFetch } from './client';
import type {
  Bar,
  ChatSession,
  ChatSessionDetail,
  DayPnl,
  EarningsHistory,
  FeedNotification,
  HealthStatus,
  MarketAlert,
  PendingProposal,
  PortfolioHistory,
  PortfolioRange,
  PriceAlert,
  Report,
  ResearchArtifact,
  Snapshot,
  Strategy,
  TickerInfo,
  WatchlistMarket,
  WatchlistsState,
  WebCustomTheme,
} from './types';

export function getHealth(): Promise<HealthStatus> {
  return apiFetch<HealthStatus>('/health');
}

export function getSnapshot(): Promise<Snapshot> {
  return apiFetch<Snapshot>('/api/snapshot');
}

// With account_id (`acct_*` | `all_paper`) serv serves the curve from its own
// ~15s poller snapshots — per-account curves the broker API can't provide —
// and adds window P&L (change / realized / unrealized_change).
export function getPortfolioHistory(
  range: PortfolioRange,
  accountId?: string,
): Promise<PortfolioHistory> {
  const params = new URLSearchParams({ range });
  if (accountId) {
    params.set('account_id', accountId);
    // Scoped history must come from serv's snapshot tables (window realized/
    // unrealized fields) — a bare acct_* would hit the Lab's broker passthrough.
    params.set('source', 'snapshots');
  }
  return apiFetch<PortfolioHistory>(`/api/portfolio/history?${params.toString()}`);
}

// Realized (net-cash, ET-day) day P&L for a scope id (e.g. `all_paper`, `all_real`,
// `schwab_<last4>`). Matches the web toolbar DAY tile; fixes the after-hours-leak the
// portfolio-history equity curve carries.
export function getDayPnl(accountId: string): Promise<DayPnl> {
  return apiFetch<DayPnl>(
    `/api/performance/day-pnl?account_id=${encodeURIComponent(accountId)}`,
  );
}

// The active-list endpoints (/api/watchlist GET/POST/DELETE) are intentionally
// unused on mobile: they follow the DESKTOP-active list. The phone's selection
// is device-local and all edits go through the per-list PUT below.
export function getWatchlists(): Promise<WatchlistsState> {
  return apiFetch<WatchlistsState>('/api/watchlists');
}

export function updateWatchlistSymbols(id: string, symbols: string[]): Promise<WatchlistsState> {
  return apiFetch<WatchlistsState>(`/api/watchlists/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: { symbols },
  });
}

export function searchSymbols(q: string, limit = 8): Promise<string[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return apiFetch<{ symbols: string[] }>(`/api/symbols/search?${params.toString()}`).then(
    (d) => d.symbols ?? [],
  );
}

export function getWatchlistMarket(
  symbols?: string[],
): Promise<WatchlistMarket> {
  const query =
    symbols && symbols.length > 0
      ? `?symbols=${encodeURIComponent(symbols.join(','))}`
      : '';
  return apiFetch<WatchlistMarket>(`/api/watchlist/market${query}`);
}

export function getBars(
  symbol: string,
  timeframe = '1Day',
  days = 180,
): Promise<{ symbol: string; timeframe: string; bars: Bar[] }> {
  const params = new URLSearchParams({
    symbol,
    timeframe,
    days: String(days),
  });
  return apiFetch<{ symbol: string; timeframe: string; bars: Bar[] }>(
    `/api/bars?${params.toString()}`,
  );
}

/** Company profile + curated fundamentals + recent news (Finnhub, fail-soft:
 *  missing key/error degrades to empty sections, never a 500). */
export function getTicker(symbol: string): Promise<TickerInfo> {
  return apiFetch<TickerInfo>(`/api/ticker/${encodeURIComponent(symbol)}`);
}

export function getEarningsHistory(symbol: string, n = 6): Promise<EarningsHistory> {
  const params = new URLSearchParams({ symbol, n: String(n) });
  return apiFetch<EarningsHistory>(`/api/fundamentals/earnings-history?${params.toString()}`);
}

export function getPendingProposals(): Promise<{ proposals: PendingProposal[] }> {
  return apiFetch<{ proposals: PendingProposal[] }>('/api/proposals/pending');
}

export function getProposal(id: string): Promise<PendingProposal> {
  return apiFetch<PendingProposal>(`/api/proposals/${encodeURIComponent(id)}`);
}

export interface ApproveProposalResult {
  status: string;
  execution_id?: string;
  execution_mode?: string;
  reason?: string;
}

export function approveProposal(
  id: string,
  payloadHash: string,
  signal?: AbortSignal,
): Promise<ApproveProposalResult> {
  return apiFetch<ApproveProposalResult>(
    `/api/proposals/${encodeURIComponent(id)}/approve`,
    {
      method: 'POST',
      body: { payload_hash: payloadHash },
      signal,
    },
  );
}

export function rejectProposal(id: string, signal?: AbortSignal): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(
    `/api/proposals/${encodeURIComponent(id)}/reject`,
    { method: 'POST', signal },
  );
}

export function getRecentAlerts(): Promise<{ alerts: MarketAlert[] }> {
  return apiFetch<{ alerts: MarketAlert[] }>('/api/alerts/recent');
}

export function getRecentNotifications(): Promise<{ notifications: FeedNotification[]; unread: number }> {
  return apiFetch<{ notifications: FeedNotification[]; unread: number }>('/api/notifications?limit=150');
}

export function getReport(id: string): Promise<Report> {
  return apiFetch<Report>(`/api/reports/${encodeURIComponent(id)}`);
}

export function getResearch(id: string): Promise<ResearchArtifact> {
  return apiFetch<ResearchArtifact>(`/api/research/${encodeURIComponent(id)}`);
}

// --- Price alerts (user-set on stock prices) ---
export function getPriceAlerts(symbol?: string): Promise<{ alerts: PriceAlert[] }> {
  const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : '';
  return apiFetch<{ alerts: PriceAlert[] }>(`/api/price-alerts${q}`);
}

export function createPriceAlert(input: {
  symbol: string;
  price: number;
  direction: 'above' | 'below' | 'cross';
  note?: string;
  /** Persistent alert — re-arms after each crossing instead of one-shot. */
  repeat?: boolean;
}): Promise<PriceAlert> {
  return apiFetch<PriceAlert>('/api/price-alerts', { method: 'POST', body: input });
}

export function deletePriceAlert(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/price-alerts/${id}`, { method: 'DELETE' });
}

export interface RegisterDeviceInput {
  token: string;
  platform?: string;
  token_type?: string;
  account_id?: string | null;
  app_version?: string;
}

export function registerDevice(
  input: RegisterDeviceInput,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/devices', {
    method: 'POST',
    body: {
      ...input,
      platform: input.platform ?? 'ios',
      token_type: input.token_type ?? 'apns',
      app_version: input.app_version ?? APP_VERSION,
    },
  });
}

/** Register a Live Activity's per-activity APNs update token so serv can UPDATE/END
 *  that specific activity. The instance API can't surface the proposalId for a
 *  push-STARTED activity, so we send the device's push-to-start token and serv
 *  correlates to the proposal by most-recent START for that device. Contract:
 *  SERV_BRIEF_live_activity. */
export function registerLiveActivityToken(args: {
  activityUpdateToken: string;
  deviceToken: string;
  proposalId?: string;
}): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/api/live-activities', {
    method: 'POST',
    body: {
      activity_update_token: args.activityUpdateToken,
      device_token: args.deviceToken,
      ...(args.proposalId ? { proposal_id: args.proposalId } : {}),
    },
  });
}

// --- Chat sessions (persisted history) ---
export function listChatSessions(limit = 50): Promise<{ sessions: ChatSession[] }> {
  return apiFetch<{ sessions: ChatSession[] }>(`/api/chat/sessions?limit=${limit}`);
}

export function getChatSession(id: string): Promise<ChatSessionDetail> {
  return apiFetch<ChatSessionDetail>(`/api/chat/sessions/${encodeURIComponent(id)}`);
}

export function createChatSession(title: string): Promise<ChatSession> {
  return apiFetch<ChatSession>('/api/chat/sessions', {
    method: 'POST',
    body: { mode: 'standard', title },
  });
}

export function renameChatSession(id: string, title: string): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/chat/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: { title },
  });
}

export function pinChatSession(id: string, pinned: boolean): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/chat/sessions/${encodeURIComponent(id)}/pin`, {
    method: 'POST',
    body: { pinned },
  });
}

export function deleteChatSession(id: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/chat/sessions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

export function getCustomThemes(): Promise<{ custom_themes: WebCustomTheme[] }> {
  return apiFetch<{ custom_themes: WebCustomTheme[] }>('/api/workspace/custom-themes');
}

export function getSettings(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>('/api/settings');
}

export function updateSettings(patch: Record<string, unknown>): Promise<unknown> {
  return apiFetch('/api/settings', { method: 'PUT', body: patch });
}

export function getStrategies(): Promise<{ strategies: Strategy[] }> {
  return apiFetch<{ strategies: Strategy[] }>('/api/strategies');
}

export function updateStrategy(id: string, patch: Record<string, unknown>): Promise<unknown> {
  return apiFetch(`/api/strategies/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
}
