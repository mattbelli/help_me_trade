/**
 * usePullRefresh — drives a RefreshControl from USER pulls only.
 *
 * Binding RefreshControl's `refreshing` to react-query's isRefetching shows the
 * spinner on every background interval refetch too, which yanks the scroll down
 * then back up every ~15s. This isolates the spinner to an explicit pull.
 */
import { useCallback, useState } from 'react';

export function usePullRefresh(refetch: () => Promise<unknown>) {
  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  }, [refetch]);
  return { refreshing, onRefresh };
}
