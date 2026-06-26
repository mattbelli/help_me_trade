"use client";

import { useCallback, useEffect, useState } from "react";

import { fetchReports, fetchSettings, fetchPriceAlerts } from "@/lib/api";
import { listRuleEvents, listRules } from "@/lib/rules";
import { fetchStrategies } from "@/lib/strategies-api";
import { DEFAULT_REPORTS } from "@/components/settings/settings-reports-panel";

export type AutomationsOverview = {
  strategies: {
    active: number;
    lastAt: string | null;
  };
  rules: {
    active: number;
    lastAt: string | null;
  };
  reports: {
    enabled: boolean;
    hourEt: number;
    lastAt: string | null;
    nextAt: Date | null;
  };
  alerts: {
    active: number;
    lastAt: string | null;
  };
};

const EMPTY: AutomationsOverview = {
  strategies: { active: 0, lastAt: null },
  rules: { active: 0, lastAt: null },
  reports: { enabled: true, hourEt: 8, lastAt: null, nextAt: null },
  alerts: { active: 0, lastAt: null },
};

/** v1 ET approximation: treat hour_et as local hour, label "ET". */
export function computeNextReportFire(hourEt: number, enabled: boolean): Date | null {
  if (!enabled) return null;
  const next = new Date();
  next.setHours(hourEt, 0, 0, 0);
  if (next.getTime() <= Date.now()) {
    next.setDate(next.getDate() + 1);
  }
  return next;
}

export function useAutomationsOverview(
  pollMs = 45_000,
  enabled = true,
) {
  const [overview, setOverview] = useState<AutomationsOverview>(EMPTY);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    const next: AutomationsOverview = { ...EMPTY };

    await Promise.all([
      fetchStrategies()
        .then((strategies) => {
          next.strategies.active = strategies.filter((s) => s.enabled).length;
          const times = strategies
            .map((s) => Date.parse(s.updated_at))
            .filter((t) => !Number.isNaN(t));
          if (times.length > 0) {
            next.strategies.lastAt = new Date(Math.max(...times)).toISOString();
          }
        })
        .catch(() => {}),

      Promise.all([listRules(), listRuleEvents(40)])
        .then(([rules, events]) => {
          next.rules.active = rules.filter((r) => r.enabled).length;
          const newest = events[0]?.created_at ?? null;
          next.rules.lastAt = newest;
        })
        .catch(() => {}),

      Promise.all([fetchReports(1), fetchSettings()])
        .then(([reports, settings]) => {
          next.reports.lastAt = reports[0]?.generated_at ?? null;
          const reportsSettings = {
            ...DEFAULT_REPORTS,
            ...settings.reports,
          };
          next.reports.enabled = reportsSettings.morning_levels_enabled;
          next.reports.hourEt = reportsSettings.hour_et;
          next.reports.nextAt = computeNextReportFire(
            reportsSettings.hour_et,
            reportsSettings.morning_levels_enabled,
          );
        })
        .catch(() => {}),

      fetchPriceAlerts()
        .then((alerts) => {
          next.alerts.active = alerts.filter(
            (a) => !a.triggered || a.repeat,
          ).length;
          const times = alerts
            .map((a) => (a.last_triggered_at ? Date.parse(a.last_triggered_at) : NaN))
            .filter((t) => !Number.isNaN(t));
          if (times.length > 0) {
            next.alerts.lastAt = new Date(Math.max(...times)).toISOString();
          }
        })
        .catch(() => {}),
    ]);

    setOverview(next);
    setLoading(false);
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void refresh().then(() => {
      if (cancelled) return;
    });
    const timer = setInterval(() => {
      void refresh();
    }, pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [refresh, pollMs, enabled]);

  return { overview, loading, refresh };
}
