"use client";

import { SettingsSection, TerminalToggle } from "@/components/ui/terminal";
import {
  useAppearancePrefs,
  useAutoHideChrome,
} from "@/lib/workspace/use-appearance-prefs";

/**
 * Look-and-feel prefs. These live in localStorage (per browser, not in
 * `data/settings.json`) and apply the moment you flip them — no Save.
 */
export function SettingsAppearancePanel() {
  const autoHideChrome = useAutoHideChrome();
  const setAutoHideChrome = useAppearancePrefs((s) => s.setAutoHideChrome);

  return (
    <div className="space-y-5">
      <SettingsSection variant="card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="font-mono text-[length:var(--fs-body)] text-foreground">
              Auto-hide toolbars
            </p>
            <p className="font-mono text-[length:var(--fs-table)] leading-snug text-[var(--fg-dim)]">
              The top toolbar and the bottom command strip slide away, and
              widgets run edge to edge (~92px more canvas). Move the pointer to
              the top or bottom edge to bring one back.
            </p>
          </div>
          <TerminalToggle
            checked={autoHideChrome}
            onCheckedChange={setAutoHideChrome}
            aria-label="Auto-hide toolbars"
          />
        </div>
      </SettingsSection>

      <p className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
        Appearance is per-browser and applies immediately.
      </p>
    </div>
  );
}
