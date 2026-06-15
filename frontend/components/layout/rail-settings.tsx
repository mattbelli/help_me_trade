"use client";

import { Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  DEFAULT_RAIL_CONFIG,
  type RightRailConfig,
  type RailPanelId,
} from "@/lib/right-rail-config";

const PANEL_LABELS: Record<RailPanelId, { title: string; hint: string }> = {
  watchlist: { title: "Watchlist", hint: "Symbols and quick chart jump" },
  rss: { title: "RSS feed", hint: "News — coming soon" },
  insights: { title: "AI insights", hint: "Live portfolio summary on each poll" },
};

type Props = {
  config: RightRailConfig;
  onChange: (config: RightRailConfig) => void;
};

export function RailSettings({ config, onChange }: Props) {
  function togglePanel(id: RailPanelId, enabled: boolean) {
    onChange({
      ...config,
      panels: { ...config.panels, [id]: enabled },
    });
  }

  return (
    <div className="space-y-4 border-b border-border px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Settings2 className="size-4 text-muted-foreground" strokeWidth={1.75} />
          <p className="text-sm font-medium">Side panel</p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          onClick={() => onChange(DEFAULT_RAIL_CONFIG)}
        >
          Reset
        </Button>
      </div>

      <div className="space-y-3">
        {(Object.keys(PANEL_LABELS) as RailPanelId[]).map((id) => (
          <div key={id} className="flex items-start justify-between gap-3">
            <div>
              <Label className="text-xs font-medium">{PANEL_LABELS[id].title}</Label>
              <p className="text-[11px] text-muted-foreground">
                {PANEL_LABELS[id].hint}
              </p>
            </div>
            <Switch
              checked={config.panels[id]}
              onCheckedChange={(checked) => togglePanel(id, checked)}
              disabled={id === "watchlist"}
            />
          </div>
        ))}
      </div>

      {config.panels.watchlist && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="watchlist-height" className="text-xs">
              Watchlist height
            </Label>
            <span className="text-xs tabular-nums text-muted-foreground">
              {config.watchlistHeightPct}%
            </span>
          </div>
          <input
            id="watchlist-height"
            type="range"
            min={28}
            max={82}
            value={config.watchlistHeightPct}
            onChange={(e) =>
              onChange({
                ...config,
                watchlistHeightPct: Number(e.target.value),
              })
            }
            className="w-full accent-[var(--chart-accent)]"
          />
          <p className="text-[11px] text-muted-foreground">
            Drag the divider between panels to resize, or use this slider.
          </p>
        </div>
      )}
    </div>
  );
}
