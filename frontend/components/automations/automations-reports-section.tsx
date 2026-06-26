"use client";

import { useCallback, useEffect, useState } from "react";

import { ReportsWidget } from "@/components/workspace/widgets/reports-widget";
import {
  DEFAULT_REPORTS,
  SettingsReportsPanel,
} from "@/components/settings/settings-reports-panel";
import {
  fetchSettings,
  saveSettings,
  type AppSettings,
  type ReportsSettings,
} from "@/lib/api";
import { dispatchSettingsSaved } from "@/lib/settings-events";

export function AutomationsReportsSection() {
  const [reports, setReports] = useState<ReportsSettings>(DEFAULT_REPORTS);
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
    async (nextReports: ReportsSettings) => {
      setReports(nextReports);
      if (!fullSettings) return;
      setSaving(true);
      setError(null);
      try {
        const saved = await saveSettings({ ...fullSettings, reports: nextReports });
        setFullSettings(saved);
        setReports({ ...DEFAULT_REPORTS, ...saved.reports });
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
        onChange={(v) => void handleChange(v)}
      />
      <div className="overflow-hidden rounded-lg border border-border/80 bg-card/20">
        <div className="min-h-[320px]">
          <ReportsWidget />
        </div>
      </div>
    </div>
  );
}
