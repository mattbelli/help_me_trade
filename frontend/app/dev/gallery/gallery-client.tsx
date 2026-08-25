"use client";

import { Component, useEffect, useRef, useState, type ReactNode } from "react";

// Side-effect import: installs the fetch/WebSocket shims at module scope, which
// is BEFORE any widget effect can fire its first request. Keep it first.
import {
  UNMATCHED_EVENT,
  unmatchedPaths,
} from "@/lib/dev/gallery-env";
import { GALLERY_STATES, isGalleryState, type GalleryState } from "@/lib/dev/fixtures/routes";
import { GRID_COL_TARGET_PX, GRID_MARGIN_PX } from "@/lib/workspace/grid-metrics";
import type { WidgetInstanceConfig } from "@/lib/workspace/layout-persistence";
import {
  WIDGET_REGISTRY,
  type WidgetDefinition,
} from "@/lib/workspace/widget-registry";

/** Contact-sheet frame — one fixed size for every widget so the sheet reads as
 *  a grid and a regression in one widget is obvious next to the others. */
const SHEET_W = 560;
const SHEET_H = 360;

const ROW_HEIGHT_PX = 48;

/** The real pixel size this widget gets in the workspace, from its registry
 *  defaultSize — so an isolated shot is the size the founder actually sees. */
function workspacePixels(def: WidgetDefinition): { width: number; height: number } {
  const { w, h } = def.defaultSize;
  return {
    width: w * GRID_COL_TARGET_PX + (w - 1) * GRID_MARGIN_PX,
    height: h * ROW_HEIGHT_PX + (h - 1) * GRID_MARGIN_PX,
  };
}

/** Per-widget starting config. Widgets not named here mount with `{}` — the
 *  list is an override map, never the source of which widgets exist. */
const CONFIG: Record<string, WidgetInstanceConfig> = {
  chart: { symbol: "AAPL", timeframe: "1D", showSessionNodes: true },
  watchlist: { watchlistId: "core" },
  "company-info": { symbol: "AAPL" },
  news: { newsScope: "watchlist" },
  "options-chain": { symbol: "SPY", strikes: 10 },
  "index-pressure": { pressureWindow: "open" },
  "trade-ticket": { symbol: "AAPL", ticketAccountId: "acct_paper_main" },
  dom: { symbol: "AAPL", ticketAccountId: "acct_paper_main" },
};

/** Widgets that render an empty prompt until the founder presses a button — the
 *  scan widget deliberately does not auto-run. The harness presses it once so
 *  the shot shows rows instead of "Pick a scan and Run." Value = the button's
 *  exact label. */
const AUTORUN: Record<string, string> = { scans: "Run" };

class FrameBoundary extends Component<
  { id: string; children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--c-red)]">
          <p>threw while rendering</p>
          <p className="mt-1 text-[length:var(--fs-table)] break-words text-[var(--fg-dim)]">
            {this.state.error.message}
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

function Frame({
  def,
  state,
  width,
  height,
}: {
  def: WidgetDefinition;
  state: GalleryState;
  width: number;
  height: number;
}) {
  const Widget = def.component;
  const [config, setConfig] = useState<WidgetInstanceConfig>(
    () => CONFIG[def.id] ?? {},
  );
  const frameRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const label = AUTORUN[def.id];
    if (!label || state === "loading" || state === "error") return;
    // One delayed pass, after the widget's own first fetch (its preset list)
    // has landed and enabled the button.
    const timer = setTimeout(() => {
      const buttons = frameRef.current?.querySelectorAll("button") ?? [];
      for (const button of buttons) {
        if (button.textContent?.trim() === label) {
          button.click();
          return;
        }
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [def.id, state]);

  return (
    <div className="flex flex-col gap-1.5">
      {/* Label lives OUTSIDE the frame so it can never be mistaken for chrome
          the widget itself draws. */}
      <div className="flex items-baseline gap-2 font-mono text-[11px]">
        <span className="font-semibold text-[var(--c-accent)]">{def.id}</span>
        <span className="text-[var(--fg-faint)]">
          {width}×{height}
        </span>
        <a
          className="text-[var(--fg-faint)] underline-offset-2 hover:text-foreground hover:underline"
          href={`/dev/gallery?widget=${encodeURIComponent(def.id)}&state=${state}`}
        >
          isolate
        </a>
      </div>
      <div
        ref={frameRef}
        data-testid={`widget-frame-${def.id}`}
        style={{ width, height }}
        className="flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)]"
      >
        {/* Mirrors the workspace card header (h-10, divider, header surface) so
            the frame is a fair likeness of the real widget, not a bare body. */}
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--line-divider)] bg-[var(--surface-header)] px-3">
          <span
            className="min-w-0 truncate text-[length:var(--fs-widget-title)] font-semibold text-[var(--fg-widget-title)]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            {def.title}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <FrameBoundary id={def.id}>
            <Widget
              instanceId={`gallery-${def.id}`}
              config={config}
              onConfigChange={(patch) =>
                setConfig((prev) => ({ ...prev, ...patch }))
              }
            />
          </FrameBoundary>
        </div>
      </div>
    </div>
  );
}

function UnmatchedFooter() {
  const [paths, setPaths] = useState<string[]>([]);

  useEffect(() => {
    const sync = () => setPaths(unmatchedPaths());
    sync();
    window.addEventListener(UNMATCHED_EVENT, sync);
    const timer = setInterval(sync, 1000);
    return () => {
      window.removeEventListener(UNMATCHED_EVENT, sync);
      clearInterval(timer);
    };
  }, []);

  if (paths.length === 0) return null;

  return (
    <footer className="mt-8 border-t border-[var(--border)] pt-3 font-mono text-[11px] text-[var(--fg-dim)]">
      <p className="text-[var(--c-amber)]">
        {paths.length} endpoint(s) hit with no fixture — served {"{}"}:
      </p>
      <ul className="mt-1 space-y-0.5">
        {paths.map((p) => (
          <li key={p} className="text-[var(--fg-faint)]">
            {p}
          </li>
        ))}
      </ul>
    </footer>
  );
}

export function GalleryClient({
  widgetId,
  stateParam,
}: {
  widgetId: string | null;
  stateParam: string | null;
}) {
  const state: GalleryState = isGalleryState(stateParam) ? stateParam : "default";
  const only = widgetId
    ? WIDGET_REGISTRY.filter((w) => w.id === widgetId)
    : null;
  const shown = only ?? WIDGET_REGISTRY;

  return (
    <div className="min-h-svh bg-background p-6">
      {/* The Terminal strip is a fixed viewport overlay mounted by AppShell; it
          would sit on top of any frame scrolled near the viewport bottom and
          land in the screenshot. Hidden for the gallery only. */}
      <style>{`body > div.pointer-events-none.fixed.inset-0.z-50 { display: none !important; }`}</style>

      <header className="mb-6 font-mono">
        <h1 className="text-[length:var(--fs-title)] font-semibold text-foreground">
          widget gallery
          <span className="ml-2 text-[var(--fg-faint)]">
            {shown.length} of {WIDGET_REGISTRY.length}
          </span>
        </h1>
        <p className="mt-1 text-[11px] text-[var(--fg-dim)]">
          Every widget in <code>widget-registry.tsx</code>, rendered against
          frozen fixtures. Dev only — no backend, no live data.
        </p>
        <nav className="mt-2 flex flex-wrap items-center gap-3 text-[11px]">
          {GALLERY_STATES.map((s) => (
            <a
              key={s}
              href={`/dev/gallery?${widgetId ? `widget=${encodeURIComponent(widgetId)}&` : ""}state=${s}`}
              className={
                s === state
                  ? "text-[var(--c-accent)]"
                  : "text-[var(--fg-dim)] underline-offset-2 hover:text-foreground hover:underline"
              }
            >
              {s}
            </a>
          ))}
          {widgetId ? (
            <a
              href={`/dev/gallery?state=${state}`}
              className="text-[var(--fg-dim)] underline-offset-2 hover:text-foreground hover:underline"
            >
              ← all widgets
            </a>
          ) : null}
        </nav>
      </header>

      {only && only.length === 0 ? (
        <p className="font-mono text-[length:var(--fs-body)] text-[var(--c-red)]">
          no widget with id &quot;{widgetId}&quot; in WIDGET_REGISTRY
        </p>
      ) : (
        <div
          data-testid="gallery-sheet"
          className="flex flex-wrap items-start gap-6"
        >
          {shown.map((def) => {
            const size = only ? workspacePixels(def) : { width: SHEET_W, height: SHEET_H };
            return (
              <Frame
                key={def.id}
                def={def}
                state={state}
                width={size.width}
                height={size.height}
              />
            );
          })}
        </div>
      )}

      <UnmatchedFooter />
    </div>
  );
}
