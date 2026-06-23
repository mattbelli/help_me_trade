/**
 * In-memory event store for the Inbox. The cockpit's single market WebSocket
 * pushes every event here; the Inbox reads it (seeded from /api/alerts/recent).
 * Also tracks per-event read state so the Inbox can mark unread + float them up.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';

import type { MarketAlert } from '@/api/types';

const MAX_ALERTS = 200;

interface AlertsValue {
  alerts: MarketAlert[];
  pushAlert: (alert: MarketAlert) => void;
  seed: (alerts: MarketAlert[]) => void;
  readIds: Set<string>;
  isRead: (id: string) => boolean;
  markRead: (id: string) => void;
  markAllRead: () => void;
}

const AlertsContext = createContext<AlertsValue | null>(null);

export function AlertsProvider({ children }: { children: ReactNode }) {
  const [alerts, setAlerts] = useState<MarketAlert[]>([]);
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const seenIds = useRef<Set<string>>(new Set());

  const pushAlert = useCallback((alert: MarketAlert) => {
    if (alert.id && seenIds.current.has(alert.id)) return;
    if (alert.id) seenIds.current.add(alert.id);
    setAlerts((prev) => [alert, ...prev].slice(0, MAX_ALERTS));
  }, []);

  const seed = useCallback((next: MarketAlert[]) => {
    seenIds.current = new Set(next.map((a) => a.id).filter(Boolean) as string[]);
    setAlerts(next.slice(0, MAX_ALERTS));
  }, []);

  const isRead = useCallback((id: string) => readIds.has(id), [readIds]);
  const markRead = useCallback((id: string) => setReadIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id))), []);
  const markAllRead = useCallback(
    () => setAlerts((cur) => { setReadIds(new Set(cur.map((a) => a.id).filter(Boolean) as string[])); return cur; }),
    [],
  );

  const value = useMemo<AlertsValue>(
    () => ({ alerts, pushAlert, seed, readIds, isRead, markRead, markAllRead }),
    [alerts, pushAlert, seed, readIds, isRead, markRead, markAllRead],
  );
  return <AlertsContext.Provider value={value}>{children}</AlertsContext.Provider>;
}

export function useAlerts(): AlertsValue {
  const ctx = useContext(AlertsContext);
  if (!ctx) throw new Error('useAlerts must be used within AlertsProvider');
  return ctx;
}
