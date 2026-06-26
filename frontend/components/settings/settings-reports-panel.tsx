"use client";

import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { ReportsSettings } from "@/lib/api";

export const DEFAULT_REPORTS: ReportsSettings = {
  morning_levels_enabled: true,
  hour_et: 8,
};

type Props = {
  value: ReportsSettings | undefined;
  onChange: (reports: ReportsSettings) => void;
};

export function SettingsReportsPanel({ value, onChange }: Props) {
  const reports = value ?? DEFAULT_REPORTS;

  function patch(partial: Partial<ReportsSettings>) {
    onChange({ ...reports, ...partial });
  }

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm font-medium">Reports</p>
        <p className="text-xs text-muted-foreground">
          Scheduled pre-market summaries delivered as phone push and in-app cards.
        </p>
      </div>

      <div className="space-y-3 rounded-lg border border-border p-3">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium">Morning levels report</p>
            <p className="text-xs text-muted-foreground">
              SPY session-node reference levels (POC/VAH/VAL/PDH/PDL) each
              trading day before the open.
            </p>
          </div>
          <Switch
            checked={reports.morning_levels_enabled}
            onCheckedChange={(checked) =>
              patch({ morning_levels_enabled: checked })
            }
          />
        </div>

        <div className="grid max-w-[8rem] gap-1.5">
          <Label htmlFor="morning-report-hour">Hour (ET)</Label>
          <input
            id="morning-report-hour"
            type="number"
            min={0}
            max={23}
            className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
            value={reports.hour_et}
            onChange={(e) => {
              const parsed = Number.parseInt(e.target.value, 10);
              if (Number.isNaN(parsed)) return;
              patch({ hour_et: Math.max(0, Math.min(23, parsed)) });
            }}
          />
          <p className="text-[11px] text-muted-foreground">
            Default 8:00am ET. Push fires once during the first 15 minutes of this
            hour.
          </p>
        </div>
      </div>
    </div>
  );
}
