"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { InsightsPanel } from "@/components/dashboard/insights-panel";
import { RailSettings } from "@/components/layout/rail-settings";
import { WatchlistPanel } from "@/components/trading/watchlist-panel";
import { Button } from "@/components/ui/button";
import {
  DEFAULT_RAIL_CONFIG,
  loadRailConfig,
  saveRailConfig,
  type RightRailConfig,
} from "@/lib/right-rail-config";
import { cn } from "@/lib/utils";

function RssPlaceholder() {
  return (
    <div className="flex flex-1 flex-col overflow-hidden p-3">
      <p className="text-sm font-medium">RSS feed</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Subreddits and news sources will appear here in a later bullet.
      </p>
    </div>
  );
}

function RightRailInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [config, setConfig] = useState<RightRailConfig>(DEFAULT_RAIL_CONFIG);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const activeSymbol =
    pathname === "/trading"
      ? (searchParams.get("symbol")?.trim().toUpperCase() ?? undefined)
      : undefined;

  useEffect(() => {
    setConfig(loadRailConfig());
    setMounted(true);
  }, []);

  const persist = useCallback((next: RightRailConfig) => {
    setConfig(next);
    saveRailConfig(next);
  }, []);

  function toggleOpen() {
    persist({ ...config, open: !config.open });
  }

  function handleWatchlistSelect(sym: string) {
    const upper = sym.trim().toUpperCase();
    if (!upper) return;
    if (pathname === "/trading") {
      router.replace(`/trading?symbol=${encodeURIComponent(upper)}`, {
        scroll: false,
      });
    } else {
      router.push(`/trading?symbol=${encodeURIComponent(upper)}`);
    }
  }

  function onDividerPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    draggingRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onDividerPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!draggingRef.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const pct = (y / rect.height) * 100;
    persist({
      ...config,
      watchlistHeightPct: Math.min(82, Math.max(28, Math.round(pct))),
    });
  }

  function onDividerPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    draggingRef.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
  }

  const bottomPanelsEnabled =
    (config.panels.rss ? 1 : 0) + (config.panels.insights ? 1 : 0) > 0;

  if (!mounted) return null;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={toggleOpen}
        className="fixed right-4 top-4 z-30 hidden border-border/80 bg-card/90 backdrop-blur-sm lg:inline-flex"
        aria-expanded={config.open}
        aria-controls="right-rail"
      >
        {config.open ? "Hide panel" : "Show panel"}
      </Button>

      <aside
        id="right-rail"
        className={cn(
          "hidden h-svh shrink-0 border-l border-border bg-sidebar/80 transition-[width,opacity] duration-[320ms] ease-[cubic-bezier(0.16,1,0.3,1)] lg:flex lg:flex-col",
          config.open ? "w-80 opacity-100" : "w-0 overflow-hidden border-l-0 opacity-0",
        )}
      >
        <div className="flex w-80 shrink-0 items-center justify-between border-b border-border px-3 py-2.5">
          <p className="text-sm font-medium tracking-tight">Side panel</p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={() => setSettingsOpen((v) => !v)}
          >
            {settingsOpen ? "Done" : "Configure"}
          </Button>
        </div>

        {settingsOpen && (
          <RailSettings config={config} onChange={persist} />
        )}

        <div ref={containerRef} className="flex min-h-0 flex-1 flex-col">
          {config.panels.watchlist && (
            <div
              className="flex min-h-0 flex-col overflow-hidden border-b border-border"
              style={{
                flex: bottomPanelsEnabled
                  ? `0 0 ${config.watchlistHeightPct}%`
                  : "1 1 auto",
              }}
            >
              <WatchlistPanel
                variant="rail"
                activeSymbol={activeSymbol}
                onSelect={handleWatchlistSelect}
                className="h-full min-h-0"
              />
            </div>
          )}

          {config.panels.watchlist && bottomPanelsEnabled && (
            <div
              role="separator"
              aria-orientation="horizontal"
              aria-label="Resize watchlist"
              className="group flex h-2 shrink-0 cursor-row-resize items-center justify-center bg-border/40 hover:bg-border"
              onPointerDown={onDividerPointerDown}
              onPointerMove={onDividerPointerMove}
              onPointerUp={onDividerPointerUp}
            >
              <span className="h-0.5 w-10 rounded-full bg-muted-foreground/40 group-hover:bg-muted-foreground/70" />
            </div>
          )}

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {config.panels.rss && <RssPlaceholder />}
            {config.panels.insights && (
              <div className="flex min-h-0 flex-1 flex-col p-3">
                <InsightsPanel />
              </div>
            )}
            {!bottomPanelsEnabled && !config.panels.watchlist && (
              <p className="p-4 text-sm text-muted-foreground">
                Enable a panel in Configure.
              </p>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}

export function RightRail() {
  return <RightRailInner />;
}
