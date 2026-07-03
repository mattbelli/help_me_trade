import { APP_VERSION } from '@/config';

import { apiFetch } from './client';
import type {
  Bar,
  EarningsHistory,
  FeedNotification,
  HealthStatus,
  MarketAlert,
  PendingProposal,
  PortfolioHistory,
  PortfolioRange,
  PriceAlert,
  Snapshot,
  WatchlistMarket,
  WatchlistsState,
} from './types';

export function getHealth(): Promise<HealthStatus> {
  return apiFetch<HealthStatus>('/health');
}

export function getSnapshot(): Promise<Snapshot> {
  return apiFetch<Snapshot>('/api/snapshot');
}

export function getPortfolioHistory(
  range: PortfolioRange,
): Promise<PortfolioHistory> {
  return apiFetch<PortfolioHistory>(
    `/api/portfolio/history?range=${encodeURIComponent(range)}`,
  );
}

export function getWatchlist(): Promise<{ symbols: string[] }> {
  return apiFetch<{ symbols: string[] }>('/api/watchlist');
}

export function addWatchlistSymbol(symbol: string): Promise<{ symbols: string[] }> {
  return apiFetch<{ symbols: string[] }>('/api/watchlist', {
    method: 'POST',
    body: { symbol: symbol.trim().toUpperCase() },
  });
}

export function removeWatchlistSymbol(symbol: string): Promise<{ symbols: string[] }> {
  return apiFetch<{ symbols: string[] }>(`/api/watchlist/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  });
}

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
  return apiFetch<{ notifications: FeedNotification[]; unread: number }>('/api/notifications');
}

// --- Price alerts (user-set on stock prices) ---
export function getPriceAlerts(symbol?: string): Promise<{ alerts: PriceAlert[] }> {
  const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : '';
  return apiFetch<{ alerts: PriceAlert[] }>(`/api/price-alerts${q}`);
}

export function createPriceAlert(input: {
  symbol: string;
  price: number;
  direction: 'above' | 'below';
  note?: string;
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
