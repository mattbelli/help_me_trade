"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactGridLayout, { type Layout } from "react-grid-layout/legacy";

import { PageHeader } from "@/components/layout/page-header";
import { TickerSearch } from "@/components/trading/ticker-search";
import { TvLightChart } from "@/components/trading/tv-light-chart";
import { Button } from "@/components/ui/button";
import {
  fetchChartLayout,
  saveChartLayout,
  type ChartLayoutItem,
} from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";

type GridItem = ChartLayoutItem;

export function ChartsGridPage() {
  const [items, setItems] = useState<GridItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [width, setWidth] = useState(1200);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editQuery, setEditQuery] = useState("");

  useEffect(() => {
    fetchChartLayout()
      .then(setItems)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    function measure() {
      const el = document.getElementById("charts-grid-root");
      if (el) setWidth(el.clientWidth);
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const layout: Layout = useMemo(
    () =>
      items.map(({ i, x, y, w, h }) => ({
        i,
        x,
        y,
        w,
        h,
        minW: 2,
        minH: 3,
      })),
    [items],
  );

  const persist = useCallback(async (next: GridItem[]) => {
    setItems(next);
    try {
      await saveChartLayout(next);
    } catch {
      /* keep local state */
    }
  }, []);

  function onLayoutChange(nextLayout: Layout) {
    const merged = items.map((item) => {
      const cell = nextLayout.find((l) => l.i === item.i);
      if (!cell) return item;
      return {
        ...item,
        x: cell.x,
        y: cell.y,
        w: cell.w,
        h: cell.h,
      };
    });
    persist(merged);
  }

  function updateSymbol(id: string, symbol: string) {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    persist(items.map((it) => (it.i === id ? { ...it, symbol: sym } : it)));
    setEditingId(null);
    setEditQuery("");
  }

  if (loading) {
    return <Skeleton className="h-[600px] w-full rounded-lg" />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Charts"
        subtitle="Drag and resize panels. Layout saves automatically."
      />

      <div id="charts-grid-root" className="min-h-[640px]">
        <ReactGridLayout
          className="layout"
          layout={layout}
          cols={12}
          rowHeight={48}
          width={width}
          margin={[8, 8]}
          containerPadding={[0, 0]}
          onDragStop={onLayoutChange}
          onResizeStop={onLayoutChange}
          draggableHandle=".chart-drag-handle"
        >
          {items.map((item) => (
            <div
              key={item.i}
              className="flex flex-col overflow-hidden rounded-lg border border-border bg-card"
            >
              <div className="chart-drag-handle flex cursor-grab items-center justify-between border-b border-border px-2 py-1.5 active:cursor-grabbing">
                {editingId === item.i ? (
                  <div className="flex flex-1 gap-1 pr-2">
                    <TickerSearch
                      value={editQuery}
                      onChange={setEditQuery}
                      onSelect={(sym) => updateSymbol(item.i, sym)}
                      className="flex-1"
                    />
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditingId(null)}
                    >
                      Done
                    </Button>
                  </div>
                ) : (
                  <>
                    <button
                      type="button"
                      className="text-sm font-medium tracking-tight"
                      onClick={() => {
                        setEditingId(item.i);
                        setEditQuery(item.symbol);
                      }}
                    >
                      {item.symbol}
                    </button>
                    <span className="text-[10px] text-muted-foreground">
                      drag
                    </span>
                  </>
                )}
              </div>
              <div className="min-h-0 flex-1">
                <TvLightChart symbol={item.symbol} compact />
              </div>
            </div>
          ))}
        </ReactGridLayout>
      </div>
    </div>
  );
}
