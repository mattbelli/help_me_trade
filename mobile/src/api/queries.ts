import { useAuth } from '@/auth/auth-context';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  addWatchlistSymbol,
  approveProposal,
  createPriceAlert,
  deletePriceAlert,
  getBars,
  getHealth,
  getPendingProposals,
  getPortfolioHistory,
  getPriceAlerts,
  getRecentAlerts,
  getSnapshot,
  getWatchlist,
  getWatchlistMarket,
  getWatchlists,
  rejectProposal,
  removeWatchlistSymbol,
  searchSymbols,
  updateWatchlistSymbols,
} from './endpoints';
import type { WatchlistsState } from './types';
import type { PortfolioRange } from './types';

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
  const symbolsKey = symbols?.slice().sort().join(',') ?? '';

  return useQuery({
    queryKey: ['watchlist-market', symbolsKey],
    queryFn: () => getWatchlistMarket(symbols),
    enabled: enabled && Boolean(symbolsKey),
    refetchInterval: 6_000,
    staleTime: 4_000,
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
