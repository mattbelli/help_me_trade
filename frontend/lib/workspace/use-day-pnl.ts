"use client";

import { useEffect, useState } from "react";

import { fetchPortfolioHistory } from "@/lib/api";
import { fetchDayPnl } from "@/lib/performance-api";

export function useDayPnl() {
  const [dayPnl, setDayPnl] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    fetchPortfolioHistory("1D")
      .then((data) => {
        if (cancelled) return;
        const last = data.points.at(-1);
        setDayPnl(last?.profit_loss ?? null);
      })
      .catch(() => {
        if (!cancelled) setDayPnl(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    const id = setInterval(() => {
      fetchPortfolioHistory("1D")
        .then((data) => {
          if (cancelled) return;
          const last = data.points.at(-1);
          setDayPnl(last?.profit_loss ?? null);
        })
        .catch(() => {});
    }, 20_000);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return { dayPnl, loading };
}

/**
 * Day P&L for a ledger-backed (real/Schwab) account, sourced from the same
 * per-ET-day realized net-cash figure the trading calendar shows — so the
 * toolbar DAY tile agrees with the calendar by construction. Follows the
 * workspace account selector via `accountId`.
 */
export function useLedgerDayPnl(accountId: string) {
  const [dayPnl, setDayPnl] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!accountId) {
      setDayPnl(null);
      setLoading(false);
      return;
    }
    let cancelled = false;

    const load = () =>
      fetchDayPnl({ account_id: accountId })
        .then((data) => {
          if (cancelled) return;
          setDayPnl(data.has_activity ? data.day_pnl : 0);
        })
        .catch(() => {
          if (!cancelled) setDayPnl(null);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });

    setLoading(true);
    load();
    const id = setInterval(load, 20_000);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [accountId]);

  return { dayPnl, loading };
}
