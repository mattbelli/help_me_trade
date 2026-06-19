"use client";

import { useCallback, useEffect, useMemo, useState, type MouseEvent, type PointerEvent } from "react";
import ReactGridLayout, { type Layout } from "react-grid-layout/legacy";
import { ChevronDown, X } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CHART_TIMEFRAMES,
  type ChartTimeframe,
} from "@/lib/chart-time";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { cn } from "@/lib/utils";
import {
  clampLayoutWidth,
  deriveGridCols,
  GRID_COL_TARGET_PX,
  GRID_DEFAULT_COLS,
  GRID_MARGIN_PX,
  gridPixelWidth,
} from "@/lib/workspace/grid-metrics";
import type { WidgetInstanceConfig } from "@/lib/workspace/layout-persistence";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { getWidgetDefinition } from "@/lib/workspace/widget-registry";

import { ChannelSelector } from "./channel-selector";
import { WatchlistListSelector } from "./widgets/watchlist-list-selector";
import { WorkspaceWidgetPicker } from "./workspace-widget-picker";

const ROW_HEIGHT_PX = 48;

const CHART_STYLES = [
  { label: "Candles", value: "candle" as const },
  { label: "Line", value: "line" as const },
];

function resolveChartTimeframe(value?: string): ChartTimeframe {
  return (
    CHART_TIMEFRAMES.find((tf) => tf.value === value) ?? CHART_TIMEFRAMES[4]
  );
}

const dragSafeControlProps = {
  onClick: (e: MouseEvent) => e.stopPropagation(),
  onPointerDown: (e: PointerEvent) => e.stopPropagation(),
};

function ChartHeaderControls({
  config,
  channel,
  onConfigChange,
}: {
  config: WidgetInstanceConfig;
  channel: ChannelId;
  onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
}) {
  const channelSymbol = useChannelSymbol(channel);
  const manualMode = channel === "none";
  const timeframe = resolveChartTimeframe(config.timeframe);
  const style = config.style ?? "candle";
  const symbol =
    channel !== "none"
      ? (channelSymbol ?? config.symbol ?? "SPY")
      : (config.symbol ?? "SPY");
  const styleLabel =
    CHART_STYLES.find((s) => s.value === style)?.label ?? "Candles";

  return (
    <>
      {manualMode ? (
        <input
          type="text"
          value={config.symbol ?? "SPY"}
          onChange={(e) =>
            onConfigChange({
              symbol: e.target.value.toUpperCase().trim() || "SPY",
            })
          }
          {...dragSafeControlProps}
          className="workspace-no-drag w-16 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-1.5 py-0.5 font-mono text-[11px] font-semibold text-foreground uppercase outline-none focus:border-[var(--c-accent)]"
        />
      ) : (
        <span className="font-mono text-[11px] font-semibold text-[var(--c-accent)]">
          {symbol}
        </span>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger
          type="button"
          {...dragSafeControlProps}
          className="workspace-no-drag inline-flex items-center gap-0.5 rounded px-1 py-0.5 font-mono text-[10px] text-[var(--fg-dim)] outline-none hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          {timeframe.label}
          <ChevronDown className="size-2.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="min-w-24 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[11px] shadow-[var(--panel-shadow)]"
        >
          {CHART_TIMEFRAMES.map((tf) => (
            <DropdownMenuItem
              key={tf.value}
              onClick={() => onConfigChange({ timeframe: tf.value })}
              className={cn(
                "px-2 py-1.5",
                timeframe.value === tf.value
                  ? "text-foreground"
                  : "text-[var(--fg-dim)]",
              )}
            >
              {tf.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger
          type="button"
          {...dragSafeControlProps}
          className="workspace-no-drag inline-flex items-center gap-0.5 rounded px-1 py-0.5 font-mono text-[10px] text-[var(--fg-dim)] outline-none hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          {styleLabel}
          <ChevronDown className="size-2.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="min-w-24 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[11px] shadow-[var(--panel-shadow)]"
        >
          {CHART_STYLES.map((s) => (
            <DropdownMenuItem
              key={s.value}
              onClick={() => onConfigChange({ style: s.value })}
              className={cn(
                "px-2 py-1.5",
                style === s.value ? "text-foreground" : "text-[var(--fg-dim)]",
              )}
            >
              {s.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

function useWidgetHeaderMeta(widgetType: string): string | undefined {
  const { snapshot } = useSnapshot();
  const def = getWidgetDefinition(widgetType);

  if (def?.meta) return def.meta;

  if (widgetType === "open-orders" && snapshot) {
    const count = snapshot.open_order_count;
    return count === 1 ? "1 open" : `${count} open`;
  }

  return undefined;
}

function WidgetRemoveButton({
  title,
  onRemove,
}: {
  title: string;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        type="button"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        className="workspace-no-drag rounded p-0.5 font-mono text-[12px] text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
        aria-label={`Remove ${title}`}
      >
        <X className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-44 border-[var(--border)] bg-[var(--panel)] p-2 font-mono shadow-[var(--panel-shadow)]"
      >
        <p className="px-1 py-1 text-[11px] text-[var(--fg-dim)]">
          Remove this widget?
        </p>
        <div className="mt-1.5 flex justify-end gap-1.5">
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded border border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onRemove();
            }}
            className="rounded border border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] px-2 py-0.5 text-[10px] text-[var(--c-red)] transition-colors hover:bg-[color-mix(in_srgb,var(--c-red)_12%,transparent)]"
          >
            Remove
          </button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function WidgetHeader({
  widgetType,
  channelAware,
  channel,
  onChannelChange,
  onRemove,
  watchlistId,
  onWatchlistChange,
  config,
  onConfigChange,
  locked,
}: {
  widgetType: string;
  channelAware?: boolean;
  channel: ChannelId;
  onChannelChange: (channel: ChannelId) => void;
  onRemove: () => void;
  watchlistId?: string;
  onWatchlistChange?: (watchlistId: string) => void;
  config?: WidgetInstanceConfig;
  onConfigChange?: (patch: Partial<WidgetInstanceConfig>) => void;
  locked: boolean;
}) {
  const def = getWidgetDefinition(widgetType);
  const meta = useWidgetHeaderMeta(widgetType);
  const isChart = widgetType === "chart";

  return (
    <div className="workspace-drag-handle relative flex cursor-grab items-center gap-2 border-b border-[var(--border)] px-3 py-2.5 active:cursor-grabbing">
      <span className="font-mono text-[12px] leading-none tracking-[-1px] text-[var(--fg-faint)]">
        ⠿
      </span>
      {isChart && config && onConfigChange ? (
        <ChartHeaderControls
          config={config}
          channel={channel}
          onConfigChange={onConfigChange}
        />
      ) : (
        <span className="font-mono text-[11.5px] font-semibold text-foreground">
          {def?.title ?? widgetType}
        </span>
      )}
      {widgetType === "watchlist" && onWatchlistChange ? (
        <WatchlistListSelector
          watchlistId={watchlistId}
          onSelect={onWatchlistChange}
        />
      ) : null}
      {meta ? (
        <span className="font-mono text-[10px] text-[var(--fg-faint)]">
          {meta}
        </span>
      ) : null}
      <div className="flex-1" />
      {channelAware ? (
        <ChannelSelector channel={channel} onChange={onChannelChange} />
      ) : null}
      {!locked ? (
        <WidgetRemoveButton
          title={def?.title ?? widgetType}
          onRemove={onRemove}
        />
      ) : null}
    </div>
  );
}

export function WorkspaceGrid() {
  const {
    layout,
    hydrated,
    hydrate,
    locked,
    updateLayout,
    updateWidgetConfig,
    removeWidget,
    pickerOpen,
    setPickerOpen,
    setGridCols,
  } = useWorkspaceStore();
  const [width, setWidth] = useState(() =>
    gridPixelWidth(GRID_DEFAULT_COLS),
  );
  const [cols, setCols] = useState(GRID_DEFAULT_COLS);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!hydrated) return;

    const el = document.getElementById("workspace-grid-scroll");
    if (!el) return;

    function measure() {
      const availWidth = el!.clientWidth - 32;
      const nextCols = deriveGridCols(availWidth);
      setCols(nextCols);
      setGridCols(nextCols);
      setWidth(gridPixelWidth(nextCols));
    }

    measure();

    const observer = new ResizeObserver(() => measure());
    observer.observe(el);
    return () => observer.disconnect();
  }, [hydrated, setGridCols]);

  const gridLayout: Layout = useMemo(
    () =>
      layout.map(({ i, x, y, w, h, minW, minH }) => {
        const clamped = clampLayoutWidth(x, w, cols);
        return {
          i,
          x: clamped.x,
          y,
          w: clamped.w,
          h,
          minW: Math.min(minW ?? 4, cols),
          minH: minH ?? 2,
        };
      }),
    [layout, cols],
  );

  const persistLayout = useCallback(
    (nextLayout: Layout) => {
      const merged = layout.map((item) => {
        const cell = nextLayout.find((l) => l.i === item.i);
        if (!cell) return item;
        return { ...item, x: cell.x, y: cell.y, w: cell.w, h: cell.h };
      });
      updateLayout(merged);
    },
    [layout, updateLayout],
  );

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background">
      {pickerOpen ? (
        <WorkspaceWidgetPicker onClose={() => setPickerOpen(false)} />
      ) : null}

      <div
        id="workspace-grid-scroll"
        className="min-h-0 flex-1 overflow-auto px-4 pt-4 pb-6"
        style={
          locked
            ? undefined
            : {
                backgroundImage:
                  "radial-gradient(circle, color-mix(in srgb, var(--fg-faint) 30%, transparent) 1px, transparent 1px)",
                backgroundSize: `${GRID_COL_TARGET_PX + GRID_MARGIN_PX}px ${ROW_HEIGHT_PX + GRID_MARGIN_PX}px`,
                backgroundPosition: "16px 16px",
                backgroundAttachment: "local",
              }
        }
      >
        {hydrated ? (
          <div id="workspace-grid-root" className="relative" style={{ width }}>
            <ReactGridLayout
              className="layout"
              layout={gridLayout}
              cols={cols}
              rowHeight={ROW_HEIGHT_PX}
              width={width}
              margin={[GRID_MARGIN_PX, GRID_MARGIN_PX]}
              containerPadding={[0, 0]}
              compactType={null}
              preventCollision
              onDragStop={persistLayout}
              onResizeStop={persistLayout}
              draggableHandle=".workspace-drag-handle"
              draggableCancel=".workspace-no-drag"
              resizeHandles={["se", "sw"]}
              isDraggable={!locked}
              isResizable={!locked}
              useCSSTransforms={false}
            >
              {layout.map((item) => {
                const def = getWidgetDefinition(item.type);
                const Widget = def?.component;
                const config: WidgetInstanceConfig = item.config ?? {};
                const channel: ChannelId = config.channel ?? "none";

                return (
                  <div
                    key={item.i}
                    id={`widget-${item.i}`}
                    className="group flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)] transition-[border-color,box-shadow] hover:border-[var(--border-strong)]"
                  >
                    <WidgetHeader
                      widgetType={item.type}
                      channelAware={def?.channelAware}
                      channel={channel}
                      onChannelChange={(next) =>
                        updateWidgetConfig(item.i, { channel: next })
                      }
                      onRemove={() => removeWidget(item.i)}
                      watchlistId={config.watchlistId}
                      onWatchlistChange={(next) =>
                        updateWidgetConfig(item.i, { watchlistId: next })
                      }
                      config={config}
                      onConfigChange={(patch) =>
                        updateWidgetConfig(item.i, patch)
                      }
                      locked={locked}
                    />
                    <div className="min-h-0 flex-1 overflow-auto">
                      {Widget ? (
                        <Widget
                          instanceId={item.i}
                          config={config}
                          onConfigChange={(patch) =>
                            updateWidgetConfig(item.i, patch)
                          }
                        />
                      ) : (
                        <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
                          Unknown widget: {item.type}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </ReactGridLayout>
          </div>
        ) : null}
      </div>
    </div>
  );
}
