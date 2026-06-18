"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactGridLayout, { type Layout } from "react-grid-layout/legacy";
import { GripVerticalIcon } from "lucide-react";

import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";

import { AccountSummaryWidget } from "./widgets/account-summary-widget";
import { PositionsWidget } from "./widgets/positions-widget";

const WIDGET_META: Record<string, { title: string }> = {
  "account-summary": { title: "Account summary" },
  positions: { title: "Positions" },
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
  }, []);

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

  if (!hydrated) {
    return <div id="workspace-grid-root" className="min-h-64 flex-1" />;
  }

  return (
    <div id="workspace-grid-root" className="min-h-0 flex-1 p-3">
      <ReactGridLayout
        className="layout"
        layout={gridLayout}
        cols={12}
        rowHeight={48}
        width={width}
        margin={[10, 10]}
        containerPadding={[0, 0]}
        onDragStop={onLayoutChange}
        onResizeStop={onLayoutChange}
        draggableHandle=".workspace-drag-handle"
      >
        {layout.map((item) => (
          <div
            key={item.i}
            className="flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm"
          >
            <div className="workspace-drag-handle flex cursor-grab items-center gap-2 border-b border-border px-2.5 py-2 active:cursor-grabbing">
              <GripVerticalIcon className="size-3.5 text-muted-foreground" />
              <span className="font-mono text-[11px] font-semibold">
                {WIDGET_META[item.i]?.title ?? item.i}
              </span>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
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
  );
}
