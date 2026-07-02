"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchSchwabAccounts,
  type SchwabAccount,
  type SchwabAccountsResult,
} from "@/lib/api";

const POLL_MS = 30_000;

const EMPTY_TOTALS: SchwabAccountsResult["totals"] = {
  liquidation_value: 0,
  cash: 0,
  long_market_value: 0,
  day_pl: 0,
  unrealized_pl: 0,
  account_count: 0,
};

export function useRealAccounts(): {
  realAccounts: SchwabAccount[];
  realSummary: SchwabAccountsResult["totals"];
  realAuthenticated: boolean;
  realLoading: boolean;
} {
  const [realAccounts, setRealAccounts] = useState<SchwabAccount[]>([]);
  const [realSummary, setRealSummary] =
    useState<SchwabAccountsResult["totals"]>(EMPTY_TOTALS);
  const [realAuthenticated, setRealAuthenticated] = useState(false);
  const [realLoading, setRealLoading] = useState(true);
  const cancelledRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await fetchSchwabAccounts();
      if (cancelledRef.current) return;
      setRealAuthenticated(result.authenticated);
      if (result.authenticated) {
        setRealAccounts(result.accounts ?? []);
        setRealSummary(result.totals ?? EMPTY_TOTALS);
      } else {
        setRealAccounts([]);
        setRealSummary(EMPTY_TOTALS);
      }
    } catch {
      if (cancelledRef.current) return;
      setRealAuthenticated(false);
      setRealAccounts([]);
      setRealSummary(EMPTY_TOTALS);
    } finally {
      if (!cancelledRef.current) setRealLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [load]);

  return { realAccounts, realSummary, realAuthenticated, realLoading };
}
