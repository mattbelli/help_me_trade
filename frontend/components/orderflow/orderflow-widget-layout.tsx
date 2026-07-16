"use client";

/**
 * orderflow-widget-layout.tsx
 *
 * Client component: the four order-flow workspace widgets in a
 * resizable react-grid-layout grid, used by the /orderflow page.
 *
 * Reads ?call= from the URL on mount to pre-select a call.
 */

import { useEffect, useRef, useState } from "react";
import ReactGridLayout from "react-grid-layout/legacy";
import type { LayoutItem } from "react-grid-layout";
import { useSearchParams } from "next/navigation";

import { OrderflowFootprintWidget } from "@/components/workspace/widgets/orderflow-footprint-widget";
import { OrderflowDomWidget } from "@/components/workspace/widgets/orderflow-dom-widget";
import { OrderflowTapeWidget } from "@/components/workspace/widgets/orderflow-tape-widget";
import { OrderflowHeatmapWidget } from "@/components/workspace/widgets/orderflow-heatmap-widget";
import { useOrderFlowSession } from "@/lib/orderflow/use-orderflow-session";

const COLS        = 24;
const ROW_HEIGHT  = 72;
const MARGIN: [number, number] = [4, 4];

const DEFAULT_LAYOUT: LayoutItem[] = [
  { i: "footprint", x: 0,  y: 0,  w: 14, h: 12, minW: 8, minH: 6 },
  { i: "dom",       x: 14, y: 0,  w: 5,  h: 12, minW: 3, minH: 4 },
  { i: "tape",      x: 19, y: 0,  w: 5,  h: 12, minW: 3, minH: 3 },
  { i: "heatmap",   x: 0,  y: 12, w: 14, h: 6,  minW: 6, minH: 3 },
];

// react-grid-layout/legacy can fire onLayoutChange on every reconcile. Returning
// a *new* array each time feeds a fresh reference back into the grid → re-render →
// fire again = infinite render loop (React #185). Compare content and keep the old
// reference when nothing moved so React bails out of the update.
function sameLayout(a: readonly LayoutItem[], b: readonly LayoutItem[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const p = a[i];
    const q = b[i];
    if (p.i !== q.i || p.x !== q.x || p.y !== q.y || p.w !== q.w || p.h !== q.h) {
      return false;
    }
  }
  return true;
}

// ── Simple widget card with drag chrome ───────────────────────────────────────

function WidgetCard({
  title,
  children,
  ...rest
}: {
  title: string;
  children: React.ReactNode;
  [key: string]: unknown;
}) {
  return (
    <div
      className="flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)]"
      {...rest}
    >
      <div className="workspace-drag-handle flex shrink-0 cursor-grab select-none items-center gap-2 border-b border-[var(--border)] px-3 py-2 active:cursor-grabbing">
        <span className="font-mono text-[12px] leading-none tracking-[-1px] text-[var(--fg-faint)]">⠿</span>
        <span
          className="font-mono text-[11.5px] font-semibold"
          style={{ color: "var(--foreground)" }}
        >
          {title}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {children}
      </div>
    </div>
  );
}

// ── Dummy WidgetProps shim ────────────────────────────────────────────────────

const NOOP_CONFIG = {
  config: {} as Record<string, unknown>,
  onConfigChange: () => {},
} as const;

// ── Main layout component ─────────────────────────────────────────────────────

export function OrderFlowWidgetLayout() {
  const searchParams = useSearchParams();
  const callId       = useOrderFlowSession((s) => s.callId);
  const setCallId    = useOrderFlowSession((s) => s.setCallId);

  // Pre-select call from URL on first mount
  useEffect(() => {
    const id = searchParams.get("call");
    if (id && !callId) {
      setCallId(id);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Measure container width for ReactGridLayout
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1200);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setWidth((w) => (w === el.clientWidth ? w : el.clientWidth));
    const ro = new ResizeObserver(() =>
      setWidth((w) => (w === el.clientWidth ? w : el.clientWidth)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const [layout, setLayout] = useState<LayoutItem[]>(DEFAULT_LAYOUT);

  return (
    <div
      ref={containerRef}
      className="w-full"
      style={{ background: "var(--background)", padding: "4px", minHeight: "100vh" }}
    >
      <ReactGridLayout
        width={width}
        cols={COLS}
        rowHeight={ROW_HEIGHT}
        layout={layout}
        onLayoutChange={(next) =>
          setLayout((prev) => (sameLayout(prev, next) ? prev : [...next]))
        }
        isDraggable
        isResizable
        draggableHandle=".workspace-drag-handle"
        margin={MARGIN}
        containerPadding={[0, 0]}
      >
        <WidgetCard key="footprint" title="footprint">
          <OrderflowFootprintWidget
            {...NOOP_CONFIG}
            instanceId="footprint"
          />
        </WidgetCard>

        <WidgetCard key="dom" title="DOM ladder">
          <OrderflowDomWidget
            {...NOOP_CONFIG}
            instanceId="dom"
          />
        </WidgetCard>

        <WidgetCard key="tape" title="time &amp; sales">
          <OrderflowTapeWidget
            {...NOOP_CONFIG}
            instanceId="tape"
          />
        </WidgetCard>

        <WidgetCard key="heatmap" title="liquidity heatmap">
          <OrderflowHeatmapWidget
            {...NOOP_CONFIG}
            instanceId="heatmap"
          />
        </WidgetCard>
      </ReactGridLayout>
    </div>
  );
}
