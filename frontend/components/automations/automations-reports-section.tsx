"use client";

import { useCallback, useEffect, useState } from "react";

import { ReportsWidget } from "@/components/workspace/widgets/reports-widget";
import {
  DEFAULT_ALERTS,
  DEFAULT_REPORTS,
  SettingsReportsPanel,
} from "@/components/settings/settings-reports-panel";
import {
  fetchSettings,
  saveSettings,
  type AlertsSettings,
  type AppSettings,
  type ReportsSettings,
} from "@/lib/api";
import { dispatchSettingsSaved } from "@/lib/settings-events";

export function AutomationsReportsSection() {
  const [reports, setReports] = useState<ReportsSettings>(DEFAULT_REPORTS);
  const [alerts, setAlerts] = useState<AlertsSettings>(DEFAULT_ALERTS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fullSettings, setFullSettings] = useState<AppSettings | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSettings()
      .then((data) => {
        if (cancelled) return;
        setFullSettings(data);
        setReports({ ...DEFAULT_REPORTS, ...data.reports });
        setAlerts({ ...DEFAULT_ALERTS, ...data.alerts });
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load schedule");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleChange = useCallback(
    async (patch: { reports?: ReportsSettings; alerts?: AlertsSettings }) => {
      if (patch.reports) setReports(patch.reports);
      if (patch.alerts) setAlerts(patch.alerts);
      if (!fullSettings) return;
      setSaving(true);
      setError(null);
      try {
        // Section-scoped patch: spreading fullSettings would include
        // trading.kill_switch_active and trip the admin-only settings guard.
        const saved = await saveSettings(patch);
        setFullSettings(saved);
        setReports({ ...DEFAULT_REPORTS, ...saved.reports });
        setAlerts({ ...DEFAULT_ALERTS, ...saved.alerts });
        dispatchSettingsSaved();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to save schedule");
      } finally {
        setSaving(false);
      }
    },
    [fullSettings],
  );

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading schedule…</p>;
  }

  return (
    <div className="space-y-6">
      {error && <p className="text-sm text-destructive">{error}</p>}
      {saving && (
        <p className="text-xs text-muted-foreground">Saving schedule…</p>
      )}
      <SettingsReportsPanel
        value={reports}
        onChange={(v) => void handleChange({ reports: v })}
        alerts={alerts}
        onAlertsChange={(v) => void handleChange({ alerts: v })}
      />
      <div className="overflow-hidden rounded-lg border border-border/80 bg-card/20">
        <div className="min-h-[320px]">
          <ReportsWidget />
        </div>
      </div>
    </div>
  );
}
