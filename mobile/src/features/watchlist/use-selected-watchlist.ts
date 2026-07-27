/**
 * useSelectedWatchlist — the device-local watchlist selection. Saved lists are
 * shared with the web app (/api/watchlists), but WHICH list this phone shows is
 * local state persisted on-device: switching on desktop must never move the
 * phone, and vice versa (we never write /api/watchlists/active).
 *
 * The selection lives in a module-level store shared by every hook instance:
 * the screen (renders the list) and the pager (subscribes its symbols over the
 * WS) MUST agree, or the app displays list A while streaming list B — frozen
 * rows, no error. Do not lift this back into per-instance useState.
 *
 * Fallback order when the stored id is missing or its list was deleted:
 * server-active list → first list.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';

import type { WatchlistList, WatchlistsState } from '@/api/types';

const STORAGE_KEY = 'watchlist.selectedId';

let sharedId: string | null = null;
let hydrateStarted = false;
const listeners = new Set<() => void>();

function setSharedId(id: string) {
  sharedId = id;
  listeners.forEach((notify) => notify());
}

export function useSelectedWatchlist(state: WatchlistsState | undefined): {
  selected: WatchlistList | undefined;
  select: (id: string) => void;
} {
  const [, bump] = useState(0);

  useEffect(() => {
    const notify = () => bump((n) => n + 1);
    listeners.add(notify);
    if (!hydrateStarted) {
      hydrateStarted = true;
      void AsyncStorage.getItem(STORAGE_KEY).then((saved) => {
        // A user tap that raced the read wins over the persisted value.
        if (saved && sharedId == null) setSharedId(saved);
      });
    }
    return () => {
      listeners.delete(notify);
    };
  }, []);

  const select = (id: string) => {
    setSharedId(id);
    void AsyncStorage.setItem(STORAGE_KEY, id);
  };

  const lists = state?.lists ?? [];
  const selected =
    lists.find((l) => l.id === sharedId) ??
    lists.find((l) => l.id === state?.active_id) ??
    lists[0];

  return { selected, select };
}
