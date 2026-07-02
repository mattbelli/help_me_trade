"use client";

import {
  TerminalField,
  TerminalInput,
  TerminalToggle,
} from "@/components/ui/terminal";
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
          <TerminalToggle
            checked={reports.morning_levels_enabled}
            onCheckedChange={(checked) =>
              patch({ morning_levels_enabled: checked })
            }
            aria-label="Morning levels report"
          />
        </div>

        <TerminalField
          label="Hour (ET)"
          htmlFor="morning-report-hour"
          className="max-w-[8rem]"
          hint="Default 8:00am ET. Push fires once during the first 15 minutes of this hour."
        >
          <TerminalInput
            id="morning-report-hour"
            type="number"
            min={0}
            max={23}
            value={reports.hour_et}
            onChange={(e) => {
              const parsed = Number.parseInt(e.target.value, 10);
              if (Number.isNaN(parsed)) return;
              patch({ hour_et: Math.max(0, Math.min(23, parsed)) });
            }}
          />
        </TerminalField>
      </div>
    </div>
  );
}
