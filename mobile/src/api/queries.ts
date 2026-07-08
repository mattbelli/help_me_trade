import { useAuth } from '@/auth/auth-context';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  addWatchlistSymbol,
  approveProposal,
  createPriceAlert,
  deleteChatSession,
  deletePriceAlert,
  listChatSessions,
  pinChatSession,
  getBars,
  getEarningsHistory,
  getHealth,
  getPendingProposals,
  getPortfolioHistory,
  getPriceAlerts,
  getRecentAlerts,
  getReport,
  getResearch,
  getSnapshot,
  getWatchlist,
  getWatchlistMarket,
  getWatchlists,
  rejectProposal,
  removeWatchlistSymbol,
  searchSymbols,
  getSettings,
  getStrategies,
  updateSettings,
  updateStrategy,
  updateWatchlistSymbols,
} from './endpoints';
import type { PortfolioRange, WatchlistMarket, WatchlistQuote, WatchlistsState } from './types';

function useAuthenticated(enabled = true) {
  const { token, isLoading } = useAuth();
  return { enabled: enabled && !isLoading && token !== null };
}

export function useSnapshot() {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['snapshot'],
    queryFn: getSnapshot,
    enabled,
    refetchInterval: 6_000,
    staleTime: 4_000,
  });
}

export function usePortfolioHistory(range: PortfolioRange) {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['portfolio-history', range],
    queryFn: () => getPortfolioHistory(range),
    enabled,
    staleTime: 30_000,
  });
}

export function useWatchlist() {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['watchlist'],
    queryFn: getWatchlist,
    enabled,
    staleTime: 60_000,
  });
}

export function useAddWatchlistSymbol() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) => addWatchlistSymbol(symbol),
    onSuccess: (data) => {
      qc.setQueryData(['watchlist'], data);
      void qc.invalidateQueries({ queryKey: ['watchlist'] });
    },
  });
}

export function useRemoveWatchlistSymbol() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) => removeWatchlistSymbol(symbol),
    onSuccess: (data) => {
      qc.setQueryData(['watchlist'], data);
      void qc.invalidateQueries({ queryKey: ['watchlist'] });
    },
  });
}

export function useWatchlists() {
  const { enabled } = useAuthenticated();
  return useQuery({
    queryKey: ['watchlists'],
    queryFn: getWatchlists,
    enabled,
    staleTime: 300_000,
  });
}

export function useReorderWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, symbols }: { id: string; symbols: string[] }) => updateWatchlistSymbols(id, symbols),
    onSuccess: (data: WatchlistsState) => {
      qc.setQueryData(['watchlists'], data);
      const active = data.lists.find((l) => l.id === data.active_id);
      if (active) qc.setQueryData(['watchlist'], { symbols: active.symbols });
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

export function useWatchlistMarket(symbols: string[] | undefined) {
  const { enabled } = useAuthenticated();
  const qc = useQueryClient();
  const symbolsKey = symbols?.slice().sort().join(',') ?? '';

  return useQuery({
    queryKey: ['watchlist-market', symbolsKey],
    queryFn: async () => {
      const data = await getWatchlistMarket(symbols);
      const prev = qc.getQueryData<WatchlistMarket>(['watchlist-market', symbolsKey]);
      return { ...data, quotes: { ...prev?.quotes, ...data.quotes } };
    },
    enabled: enabled && Boolean(symbolsKey),
    refetchInterval: 6_000,
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
}

export function useBars(symbol: string, timeframe = '1Day', days = 180) {
  const { enabled } = useAuthenticated();

  return useQuery({
    queryKey: ['bars', symbol, timeframe, days],
    queryFn: () => getBars(symbol, timeframe, days),
    enabled: enabled && Boolean(symbol),
    staleTime: 60_000,
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
