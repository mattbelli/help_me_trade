"use client";

import { useEffect, useState } from "react";

import { fetchPortfolioHistory } from "@/lib/api";

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
