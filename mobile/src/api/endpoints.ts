import { APP_VERSION } from '@/config';

import { apiFetch } from './client';
import type {
  HealthStatus,
  MarketAlert,
  PendingProposal,
  PortfolioHistory,
  PortfolioRange,
  Snapshot,
  WatchlistMarket,
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

export function getWatchlistMarket(
  symbols?: string[],
): Promise<WatchlistMarket> {
  const query =
    symbols && symbols.length > 0
      ? `?symbols=${encodeURIComponent(symbols.join(','))}`
      : '';
  return apiFetch<WatchlistMarket>(`/api/watchlist/market${query}`);
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
): Promise<ApproveProposalResult> {
  return apiFetch<ApproveProposalResult>(
    `/api/proposals/${encodeURIComponent(id)}/approve`,
    {
      method: 'POST',
      body: { payload_hash: payloadHash },
    },
  );
}

export function rejectProposal(id: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(
    `/api/proposals/${encodeURIComponent(id)}/reject`,
    { method: 'POST' },
  );
}

export function getRecentAlerts(): Promise<{ alerts: MarketAlert[] }> {
  return apiFetch<{ alerts: MarketAlert[] }>('/api/alerts/recent');
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
