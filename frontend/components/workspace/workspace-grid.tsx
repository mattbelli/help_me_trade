"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactGridLayout, { type Layout } from "react-grid-layout/legacy";
import { X } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { getWidgetDefinition } from "@/lib/workspace/widget-registry";

import { WorkspaceWidgetPicker } from "./workspace-widget-picker";

function useWidgetHeaderMeta(widgetId: string): string | undefined {
  const { snapshot } = useSnapshot();
  const def = getWidgetDefinition(widgetId);

  if (def?.meta) return def.meta;

  if (widgetId === "open-orders" && snapshot) {
    const count = snapshot.open_order_count;
    return count === 1 ? "1 open" : `${count} open`;
  }

  if (widgetId === "equity-curve") {
    return "live";
  }

  return undefined;
}

function WidgetHeader({
  widgetId,
  onRemove,
}: {
  widgetId: string;
  onRemove: () => void;
}) {
  const def = getWidgetDefinition(widgetId);
  const meta = useWidgetHeaderMeta(widgetId);

  return (
    <div className="workspace-drag-handle flex cursor-grab items-center gap-2 border-b border-[var(--border)] px-3 py-2.5 active:cursor-grabbing">
      <span className="font-mono text-[12px] leading-none tracking-[-1px] text-[var(--fg-faint)]">
        ⠿
      </span>
      <span className="font-mono text-[11.5px] font-semibold text-foreground">
        {def?.title ?? widgetId}
      </span>
      {meta ? (
        <span className="font-mono text-[10px] text-[var(--fg-faint)]">
          {meta}
        </span>
      ) : null}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        className="ml-auto rounded p-0.5 font-mono text-[12px] text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
        aria-label={`Remove ${def?.title ?? widgetId}`}
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}

export function WorkspaceGrid() {
  const {
    layout,
    hydrated,
    hydrate,
    updateLayout,
    removeWidget,
    pickerOpen,
    openPicker,
    setPickerOpen,
  } = useWorkspaceStore();
  const [width, setWidth] = useState(1200);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

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
        minW: minW ?? 3,
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

      <div className="flex shrink-0 items-center gap-3 px-5 pt-4 pb-2.5">
        <h1 className="font-sans text-[15px] font-semibold tracking-tight text-foreground">
          Workspace
        </h1>
        <span className="rounded-full border border-[var(--border)] px-2 py-0.5 font-mono text-[11px] text-[var(--fg-dim)]">
          default layout
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={openPicker}
          className="rounded-md border border-dashed border-[var(--border-strong)] px-2.5 py-1 font-mono text-[11px] text-[var(--fg-dim)] transition-colors hover:border-[var(--c-accent)] hover:bg-[var(--c-accent-soft)] hover:text-foreground"
        >
          + Add widget · or type{" "}
          <span className="text-[var(--c-accent)]">/add</span>
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-4 pb-6">
        {hydrated ? (
          <div id="workspace-grid-root">
            <ReactGridLayout
              className="layout"
              layout={gridLayout}
              cols={12}
              rowHeight={48}
              width={width}
              margin={[12, 12]}
              containerPadding={[0, 0]}
              onDragStop={onLayoutChange}
              onResizeStop={onLayoutChange}
              draggableHandle=".workspace-drag-handle"
              useCSSTransforms={false}
            >
              {layout.map((item) => {
                const def = getWidgetDefinition(item.i);
                const Widget = def?.component;

                return (
                  <div
                    key={item.i}
                    className="group flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)] transition-[border-color,box-shadow] hover:border-[var(--border-strong)]"
                  >
                    <WidgetHeader
                      widgetId={item.i}
                      onRemove={() => removeWidget(item.i)}
                    />
                    <div className="min-h-0 flex-1 overflow-auto">
                      {Widget ? (
                        <Widget />
                      ) : (
                        <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
                          Unknown widget: {item.i}
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
