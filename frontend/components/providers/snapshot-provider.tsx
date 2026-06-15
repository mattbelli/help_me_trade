"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import {
  fetchBroker,
  fetchSnapshot,
  type BrokerInfo,
  type Snapshot,
} from "@/lib/api";

const REFRESH_MS = 20_000;

type SnapshotContextValue = {
  snapshot: Snapshot | null;
  brokerInfo: BrokerInfo | null;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
};

const SnapshotContext = createContext<SnapshotContextValue | null>(null);

export function SnapshotProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [brokerInfo, setBrokerInfo] = useState<BrokerInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [data, broker] = await Promise.all([
        fetchSnapshot(),
        fetchBroker(),
      ]);
      setSnapshot(data);
      setBrokerInfo(broker);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <SnapshotContext.Provider
      value={{ snapshot, brokerInfo, error, loading, refresh }}
    >
      {children}
    </SnapshotContext.Provider>
  );
}

export function useSnapshot() {
  const ctx = useContext(SnapshotContext);
  if (!ctx) {
    throw new Error("useSnapshot must be used within SnapshotProvider");
  }
  return ctx;
}
