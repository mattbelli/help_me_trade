import { useAuth } from '@/auth/auth-context';
import { focusManager, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import {
  approveProposal,
  createPriceAlert,
  deleteChatSession,
  deletePriceAlert,
  listChatSessions,
  pinChatSession,
  getBars,
  getDayPnl,
  getEarningsHistory,
  getHealth,
  getPendingProposals,
  getPortfolioHistory,
  getPriceAlerts,
  getRecentAlerts,
  getReport,
  getResearch,
  getSnapshot,
  getWatchlistMarket,
  getWatchlists,
  listApprovalDevices,
  rejectProposal,
  searchSymbols,
  getSettings,
  getStrategies,
  getTicker,
  updateSettings,
  updateStrategy,
  updateWatchlistSymbols,
} from './endpoints';
import type { PortfolioRange, WatchlistMarket, WatchlistQuote, WatchlistsState } from './types';

function useAuthenticated(enabled = true) {
  const { token, isLoading } = useAuth();
  return { enabled: enabled && !isLoading && token !== null };
}

/**
 * Tab-gated polling. Pager surfaces stay mounted, so a query can't stop
 * polling by unmounting — the visible tab passes poll=true, the rest false.
 *
 * Deliberately NOT RQ's `refetchInterval`: flipping it false→6000 failed to
 * reschedule on device (verified via serv access logs 2026-07-10 — the
 * resume refetch fired but no interval followed), so we own the timer. A
 * plain effect keyed on `poll` re-runs by React's contract: on flip-on it
 * refetches immediately (the catch-up) and starts a 6s interval; on flip-off
 * the cleanup kills it. Ticks skip while backgrounded via the same
 * focusManager gate RQ's own intervals use (wired to AppState in _layout).
 */
function usePollWhile(poll: boolean, refetch: () => unknown, intervalMs = 6_000) {
  const prev = useRef(poll);
  useEffect(() => {
    const was = prev.current;
    prev.current = poll;
    if (!poll) return;
    // Catch up on re-entry (poll was off), but not on a poll-on mount — the
    // query's initial fetch is already in flight there.
    if (!was) void refetch();
    const id = setInterval(() => {
      if (focusManager.isFocused()) void refetch();
    }, intervalMs);
    return () => clearInterval(id);
  }, [poll, refetch, intervalMs]);
}

export function useSnapshot(opts?: { poll?: boolean }) {
  const poll = opts?.poll ?? true;
  const { enabled } = useAuthenticated();

  const query = useQuery({
    queryKey: ['snapshot'],
    queryFn: getSnapshot,
    enabled,
    staleTime: 4_000,
  });
  usePollWhile(poll && enabled, query.refetch);
  return query;
}

export function usePortfolioHistory(
  range: PortfolioRange,
  accountId?: string,
  opts?: { poll?: boolean },
) {
  const poll = opts?.poll ?? false;
  const { enabled } = useAuthenticated();

  const query = useQuery({
    queryKey: ['portfolio-history', accountId ?? 'broker', range],
    queryFn: () => getPortfolioHistory(range, accountId),
    enabled,
    staleTime: 10_000,
  });
  // 15s keeps the headline delta and pills in step with the 6s snapshot poll
  // without hammering the (cheap, SQL-only) scoped endpoint.
  usePollWhile(poll && enabled, query.refetch, 15_000);
  return query;
}

export function useDayPnl(accountId: string, opts?: { poll?: boolean }) {
  const poll = opts?.poll ?? true;
  const { enabled } = useAuthenticated();

  const query = useQuery({
    queryKey: ['day-pnl', accountId],
    queryFn: () => getDayPnl(accountId),
    enabled,
    staleTime: 4_000,
  });
  usePollWhile(poll && enabled, query.refetch);
  return query;
}

// Saved watchlists (shared with web). Which list the phone SHOWS is
// device-local (features/watchlist/use-selected-watchlist) — all symbol edits
// go through the per-list PUT below, never the active-list endpoints.
export function useWatchlists() {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['watchlists'],
    queryFn: getWatchlists,
    enabled,
    staleTime: 60_000,
    // Track list edits made on other devices while the app stays foregrounded
    // (focus refetch only covers background→foreground) — the WS subscription
    // is derived from this data, so a stale list means stale streaming symbols.
    refetchInterval: 30_000,
  });
}

export function useReorderWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, symbols }: { id: string; symbols: string[] }) => updateWatchlistSymbols(id, symbols),
    onSuccess: (data: WatchlistsState) => {
      qc.setQueryData(['watchlists'], data);
    },
  });
}

export function useSymbolSearch(query: string) {
  const { enabled } = useAuthenticated();
  const q = query.trim();
  return useQuery({
    queryKey: ['symbol-search', q],
    queryFn: () => searchSymbols(q),
    enabled: enabled && q.length >= 1,
    staleTime: 60_000,
  });
}

export function useWatchlistMarket(symbols: string[] | undefined, opts?: { poll?: boolean }) {
  const poll = opts?.poll ?? true;
  const { enabled } = useAuthenticated();
  const qc = useQueryClient();
  const symbolsKey = symbols?.slice().sort().join(',') ?? '';

  const query = useQuery({
    queryKey: ['watchlist-market', symbolsKey],
    queryFn: async () => {
      const data = await getWatchlistMarket(symbols);
      const prev = qc.getQueryData<WatchlistMarket>(['watchlist-market', symbolsKey]);
      return { ...data, quotes: { ...prev?.quotes, ...data.quotes } };
    },
    enabled: enabled && Boolean(symbolsKey),
    staleTime: 4_000,
    // Show instantly from any already-cached watchlist-market entry (e.g. the
    // full watchlist) while the fresh single-symbol fetch lands — so tapping a
    // ticker doesn't blank the price.
    placeholderData: () => {
      if (!symbols?.length) return undefined;
      const caches = qc.getQueriesData<WatchlistMarket>({ queryKey: ['watchlist-market'] });
      const quotes: Record<string, WatchlistQuote> = {};
      for (const sym of symbols) {
        for (const [, data] of caches) {
          const q = data?.quotes?.[sym];
          if (q) {
            quotes[sym] = q;
            break;
          }
        }
      }
      return Object.keys(quotes).length ? { quotes } : undefined;
    },
  });
  usePollWhile(poll && enabled && Boolean(symbolsKey), query.refetch);
  return query;
}

export function useBars(symbol: string, timeframe = '1Day', days = 180) {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['bars', symbol, timeframe, days],
    queryFn: () => getBars(symbol, timeframe, days),
    enabled: enabled && Boolean(symbol),
    staleTime: 60_000,
    // Live frames move the forming candle; this reconciles sealed bars with
    // the vendor (the futures stream skips ticks, so live wicks run short).
    refetchInterval: timeframe === '1Day' || timeframe === '1Week' ? 300_000 : 60_000,
  });
}

export function useEarningsHistory(symbol: string, n = 6, enabledOverride = true) {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['earnings-history', symbol, n],
    queryFn: () => getEarningsHistory(symbol, n),
    enabled: enabled && enabledOverride && Boolean(symbol),
    staleTime: 60 * 60_000, // EPS history is quarterly — cache an hour
  });
}

export function useTicker(symbol: string, enabledOverride = true) {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['ticker', symbol],
    queryFn: () => getTicker(symbol),
    enabled: enabled && enabledOverride && Boolean(symbol),
    staleTime: 10 * 60_000, // profile/metrics are server-cached ~24h, news ~15min
  });
}

export function usePendingProposals() {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['proposals', 'pending'],
    queryFn: getPendingProposals,
    enabled,
    refetchInterval: 10_000,
    staleTime: 8_000,
  });
}

export function useApprovalDevices() {
  return useQuery({
    queryKey: ['approval-devices'],
    queryFn: listApprovalDevices,
    ...useAuthenticated(),
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 30_000,
  });
}

export function useSettings() {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
    enabled,
    staleTime: 60_000,
  });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => updateSettings(patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['settings'] });
    },
  });
}

export function useStrategies() {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['strategies'],
    queryFn: getStrategies,
    enabled,
    staleTime: 60_000,
  });
}

export function useUpdateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) =>
      updateStrategy(id, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['strategies'] });
    },
  });
}

export function useRecentAlerts() {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['alerts', 'recent'],
    queryFn: getRecentAlerts,
    enabled,
    refetchInterval: 15_000,
    staleTime: 12_000,
  });
}

export function useReport(id: string) {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['report', id],
    queryFn: () => getReport(id),
    enabled: enabled && !!id,
    staleTime: 5 * 60_000,
  });
}

export function useResearch(id: string) {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['research', id],
    queryFn: () => getResearch(id),
    enabled: enabled && !!id,
    staleTime: 5 * 60_000,
  });
}

export function usePriceAlerts(symbol?: string) {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['price-alerts', symbol ?? 'all'],
    queryFn: () => getPriceAlerts(symbol),
    enabled,
    staleTime: 30_000,
    // Socket invalidations make normal cross-device sync immediate. Always
    // reconcile on navigation/cold start, with a small fallback poll for a
    // direct deep link where the cockpit's shared market socket is not mounted.
    refetchOnMount: 'always',
    refetchInterval: 20_000,
  });
}

export function useCreatePriceAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createPriceAlert,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['price-alerts'] }),
  });
}

export function useDeletePriceAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deletePriceAlert,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['price-alerts'] }),
  });
}

export function useChatSessions() {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['chat-sessions'],
    queryFn: () => listChatSessions(),
    enabled,
    staleTime: 10_000,
  });
}

export function usePinChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) => pinChatSession(id, pinned),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['chat-sessions'] });
    },
  });
}

export function useDeleteChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteChatSession(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['chat-sessions'] });
    },
  });
}

export function useApproveProposal() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      id,
      payloadHash,
    }: {
      id: string;
      payloadHash: string;
    }) => approveProposal(id, payloadHash),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['proposals', 'pending'] }),
        queryClient.invalidateQueries({ queryKey: ['snapshot'] }),
      ]);
    },
  });
}

export function useRejectProposal() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => rejectProposal(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['proposals', 'pending'] }),
        queryClient.invalidateQueries({ queryKey: ['snapshot'] }),
      ]);
    },
  });
}
