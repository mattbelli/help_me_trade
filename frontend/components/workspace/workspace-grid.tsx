"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactGridLayout, { type Layout } from "react-grid-layout/legacy";
import { X } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type { ChannelId } from "@/lib/workspace/channels";
import type { WidgetInstanceConfig } from "@/lib/workspace/layout-persistence";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { getWidgetDefinition } from "@/lib/workspace/widget-registry";

import { ChannelSelector } from "./channel-selector";
import { WatchlistListSelector } from "./widgets/watchlist-list-selector";
import { WorkspaceWidgetPicker } from "./workspace-widget-picker";

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
        className="rounded p-0.5 font-mono text-[12px] text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
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
  locked,
}: {
  widgetType: string;
  channelAware?: boolean;
  channel: ChannelId;
  onChannelChange: (channel: ChannelId) => void;
  onRemove: () => void;
  watchlistId?: string;
  onWatchlistChange?: (watchlistId: string) => void;
  locked: boolean;
}) {
  const def = getWidgetDefinition(widgetType);
  const meta = useWidgetHeaderMeta(widgetType);

  return (
    <div className="workspace-drag-handle relative flex cursor-grab items-center gap-2 border-b border-[var(--border)] px-3 py-2.5 active:cursor-grabbing">
      <span className="font-mono text-[12px] leading-none tracking-[-1px] text-[var(--fg-faint)]">
        ⠿
      </span>
      <span className="font-mono text-[11.5px] font-semibold text-foreground">
        {def?.title ?? widgetType}
      </span>
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
    highlightInstanceId,
  } = useWorkspaceStore();
  const [width, setWidth] = useState(1200);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!highlightInstanceId) return;
    const el = document.getElementById(`widget-${highlightInstanceId}`);
    el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlightInstanceId, layout]);

  useEffect(() => {
    function measure() {
      const el = document.getElementById("workspace-grid-root");
      if (el) setWidth(el.clientWidth);
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [hydrated]);

  const gridLayout: Layout = useMemo(
    () =>
      layout.map(({ i, x, y, w, h, minW, minH }) => ({
        i,
        x,
        y,
        w,
        h,
        minW: minW ?? 4,
        minH: minH ?? 2,
      })),
    [layout],
  );

  const onLayoutChange = useCallback(
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
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
      {pickerOpen ? (
        <WorkspaceWidgetPicker onClose={() => setPickerOpen(false)} />
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto px-4 pt-3 pb-6">
        {hydrated ? (
          <div id="workspace-grid-root">
            <ReactGridLayout
              className="layout"
              layout={gridLayout}
              cols={24}
              rowHeight={48}
              width={width}
              margin={[12, 12]}
              containerPadding={[0, 0]}
              onDragStop={onLayoutChange}
              onResizeStop={onLayoutChange}
              draggableHandle=".workspace-drag-handle"
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
                    className={cn(
                      "group flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)] transition-[border-color,box-shadow] hover:border-[var(--border-strong)]",
                      highlightInstanceId === item.i &&
                        "animate-pulse ring-2 ring-[var(--c-command)]",
                    )}
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
