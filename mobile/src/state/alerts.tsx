/**
 * In-memory live-alert store. The cockpit holds ONE market WebSocket connection
 * and pushes every alert here; the Activity feed reads it (seeded from
 * /api/alerts/recent on mount). Kept tiny and dependency-free on purpose.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';

import type { MarketAlert } from '@/api/types';

const MAX_ALERTS = 200;

interface AlertsValue {
  alerts: MarketAlert[];
  pushAlert: (alert: MarketAlert) => void;
  /** Replace the feed (used to seed from the REST recent-alerts endpoint). */
  seed: (alerts: MarketAlert[]) => void;
}

const AlertsContext = createContext<AlertsValue | null>(null);

export function AlertsProvider({ children }: { children: ReactNode }) {
  const [alerts, setAlerts] = useState<MarketAlert[]>([]);
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

  const value = useMemo<AlertsValue>(() => ({ alerts, pushAlert, seed }), [alerts, pushAlert, seed]);
  return <AlertsContext.Provider value={value}>{children}</AlertsContext.Provider>;
}

export function useAlerts(): AlertsValue {
  const ctx = useContext(AlertsContext);
  if (!ctx) throw new Error('useAlerts must be used within AlertsProvider');
  return ctx;
}
