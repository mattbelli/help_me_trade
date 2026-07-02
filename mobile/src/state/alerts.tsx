/**
 * In-memory event store for the Inbox. The cockpit's single market WebSocket
 * pushes every event here; the Inbox reads it (seeded from /api/alerts/recent).
 * Tracks per-event read state, and a persisted "dismissed" set so Clear-read
 * removals don't bounce back when the feed re-seeds from serv's rolling buffer.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';

import type { MarketAlert } from '@/api/types';

const MAX_ALERTS = 200;
const MAX_DISMISSED = 500; // bound the persisted set so it can't grow forever
const DISMISSED_KEY = 'inbox.dismissed.v1';

interface AlertsValue {
  alerts: MarketAlert[];
  pushAlert: (alert: MarketAlert) => void;
  seed: (alerts: MarketAlert[]) => void;
  readIds: Set<string>;
  isRead: (id: string) => boolean;
  markRead: (id: string) => void;
  markAllRead: () => void;
  /** Remove every read alert from the feed and remember it as dismissed. */
  clearRead: () => void;
}

const AlertsContext = createContext<AlertsValue | null>(null);

export function AlertsProvider({ children }: { children: ReactNode }) {
  const [alerts, setAlerts] = useState<MarketAlert[]>([]);
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const seenIds = useRef<Set<string>>(new Set());
  // Ref (not state) — only consulted at push/seed/clear time, never rendered.
  const dismissedRef = useRef<Set<string>>(new Set());

  // Hydrate dismissed from storage, then drop any already-shown dismissed alerts.
  useEffect(() => {
    let cancelled = false;
    AsyncStorage.getItem(DISMISSED_KEY)
      .then((raw) => {
        if (cancelled || !raw) return;
        try {
          dismissedRef.current = new Set(JSON.parse(raw) as string[]);
          setAlerts((cur) => cur.filter((a) => !(a.id && dismissedRef.current.has(a.id))));
        } catch {
          // ignore malformed storage
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const persistDismissed = useCallback(() => {
    let ids = [...dismissedRef.current];
    if (ids.length > MAX_DISMISSED) {
      ids = ids.slice(ids.length - MAX_DISMISSED);
      dismissedRef.current = new Set(ids);
    }
    void AsyncStorage.setItem(DISMISSED_KEY, JSON.stringify(ids)).catch(() => {});
  }, []);

  const pushAlert = useCallback((alert: MarketAlert) => {
    if (alert.id && (seenIds.current.has(alert.id) || dismissedRef.current.has(alert.id))) return;
    if (alert.id) seenIds.current.add(alert.id);
    setAlerts((prev) => [alert, ...prev].slice(0, MAX_ALERTS));
  }, []);

  const seed = useCallback((next: MarketAlert[]) => {
    const fresh = next.filter((a) => !(a.id && dismissedRef.current.has(a.id)));
    seenIds.current = new Set(fresh.map((a) => a.id).filter(Boolean) as string[]);
    setAlerts(fresh.slice(0, MAX_ALERTS));
  }, []);

  const isRead = useCallback((id: string) => readIds.has(id), [readIds]);
  const markRead = useCallback((id: string) => setReadIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id))), []);
  const markAllRead = useCallback(
    () => setAlerts((cur) => { setReadIds(new Set(cur.map((a) => a.id).filter(Boolean) as string[])); return cur; }),
    [],
  );

  const clearRead = useCallback(() => {
    setAlerts((cur) => {
      const remaining: MarketAlert[] = [];
      let changed = false;
      for (const a of cur) {
        if (a.id && readIds.has(a.id)) {
          dismissedRef.current.add(a.id);
          changed = true;
        } else {
          remaining.push(a);
        }
      }
      if (changed) persistDismissed();
      return changed ? remaining : cur;
    });
  }, [readIds, persistDismissed]);

  const value = useMemo<AlertsValue>(
    () => ({ alerts, pushAlert, seed, readIds, isRead, markRead, markAllRead, clearRead }),
    [alerts, pushAlert, seed, readIds, isRead, markRead, markAllRead, clearRead],
  );
  return <AlertsContext.Provider value={value}>{children}</AlertsContext.Provider>;
}

export function useAlerts(): AlertsValue {
  const ctx = useContext(AlertsContext);
  if (!ctx) throw new Error('useAlerts must be used within AlertsProvider');
  return ctx;
}
