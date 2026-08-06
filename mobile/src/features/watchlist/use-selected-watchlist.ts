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
 * It is read through useSyncExternalStore, NOT by touching `sharedId` during
 * render. React Compiler is on (app.json experiments.reactCompiler) and cannot
 * see a mutable module variable as a dependency: reading it directly let the
 * compiler cache `selected` against `state`, so tapping a list re-rendered the
 * screen but handed back the previous list — the picker only appeared to work
 * after an app restart (fresh mount = empty memo cache).
 *
 * Fallback order when the stored id is missing or its list was deleted:
 * server-active list → first list.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSyncExternalStore } from 'react';

import type { WatchlistList, WatchlistsState } from '@/api/types';

const STORAGE_KEY = 'watchlist.selectedId';

let sharedId: string | null = null;
let hydrateStarted = false;
const listeners = new Set<() => void>();

function setSharedId(id: string) {
  sharedId = id;
  listeners.forEach((notify) => notify());
}

// Stable module-level store fns — useSyncExternalStore resubscribes if these
// change identity.
function subscribe(notify: () => void): () => void {
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
}

function getSnapshot(): string | null {
  return sharedId;
}

function select(id: string): void {
  setSharedId(id);
  void AsyncStorage.setItem(STORAGE_KEY, id);
}

export function useSelectedWatchlist(state: WatchlistsState | undefined): {
  selected: WatchlistList | undefined;
  select: (id: string) => void;
} {
  const selectedId = useSyncExternalStore(subscribe, getSnapshot);

  const lists = state?.lists ?? [];
  const selected =
    lists.find((l) => l.id === selectedId) ??
    lists.find((l) => l.id === state?.active_id) ??
    lists[0];

  return { selected, select };
}
