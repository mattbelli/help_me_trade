/**
 * Symbols a screen is showing right now (the symbol detail page). The market
 * socket subscribes these on top of the selected watchlist — serv's subscribe
 * is a replace-set, so a symbol off that list got no stream at all and its
 * chart/price only moved on the REST poll.
 */

import { useEffect, useSyncExternalStore } from 'react';

const counts = new Map<string, number>();
const listeners = new Set<() => void>();
let snapshot: string[] = [];

function update(symbol: string, delta: number) {
  const n = (counts.get(symbol) ?? 0) + delta;
  if (n > 0) counts.set(symbol, n);
  else counts.delete(symbol);
  snapshot = [...counts.keys()].sort();
  listeners.forEach((l) => l());
}

/** Keep `symbol` streaming while the calling screen is mounted. */
export function useFocusSymbol(symbol: string | null | undefined): void {
  useEffect(() => {
    if (!symbol) return;
    update(symbol, 1);
    return () => update(symbol, -1);
  }, [symbol]);
}

export function useFocusedSymbols(): string[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => snapshot,
  );
}
