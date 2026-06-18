"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactGridLayout, { type Layout } from "react-grid-layout/legacy";

import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";

import { AccountSummaryWidget } from "./widgets/account-summary-widget";
import { PositionsWidget } from "./widgets/positions-widget";

const WIDGET_META: Record<string, { title: string; meta?: string }> = {
  "account-summary": { title: "account summary", meta: "live" },
  positions: { title: "positions" },
};

export function WorkspaceGrid() {
  const { layout, hydrated, hydrate, updateLayout } = useWorkspaceStore();
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
              {layout.map((item) => (
                <div
                  key={item.i}
                  className="group flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)] transition-[border-color,box-shadow] hover:border-[var(--border-strong)]"
                >
                  <div className="workspace-drag-handle flex cursor-grab items-center gap-2 border-b border-[var(--border)] px-3 py-2.5 active:cursor-grabbing">
                    <span className="font-mono text-[12px] leading-none tracking-[-1px] text-[var(--fg-faint)]">
                      ⠿
                    </span>
                    <span className="font-mono text-[11.5px] font-semibold text-foreground">
                      {WIDGET_META[item.i]?.title ?? item.i}
                    </span>
                    {WIDGET_META[item.i]?.meta ? (
                      <span className="font-mono text-[10px] text-[var(--fg-faint)]">
                        {WIDGET_META[item.i]?.meta}
                      </span>
                    ) : null}
                  </div>
                  <div className="min-h-0 flex-1 overflow-auto">
                    {item.i === "account-summary" ? (
                      <AccountSummaryWidget />
                    ) : item.i === "positions" ? (
                      <PositionsWidget />
                    ) : null}
                  </div>
                </div>
              ))}
            </ReactGridLayout>
          </div>
        ) : null}
      </div>
    </div>
  );
}
