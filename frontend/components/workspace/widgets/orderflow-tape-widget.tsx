"use client";

/**
 * orderflow-tape-widget.tsx
 *
 * Workspace widget: Time & Sales (order tape).
 *
 * Reads from the session store: callId, cursorMs, tape (initial full tape),
 * windowedTapeEvents (±2 min around cursor, refetched per 60s bucket by
 * the footprint widget).
 *
 * Styled entirely with design tokens — no hardcoded hex.
 * Table layout mirrors positions/open-orders widgets.
 */

import { useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";
import { formatTimeMsET } from "@/lib/orderflow-api";
import { useOrderFlowSession } from "@/lib/orderflow/use-orderflow-session";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

// ── Constants ─────────────────────────────────────────────────────────────────

const ACTION_LABELS: Record<string, string> = {
  A: "Add",
  C: "Cancel",
  M: "Modify",
  T: "Trade",
  F: "Fill",
};

// ── Main widget ────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function OrderflowTapeWidget(_props: WidgetProps) {
  const callId              = useOrderFlowSession((s) => s.callId);
  const cursorMs            = useOrderFlowSession((s) => s.cursorMs);
  const tape                = useOrderFlowSession((s) => s.tape);
  const windowedTapeEvents  = useOrderFlowSession((s) => s.windowedTapeEvents);
  const windowedTapeLoading = useOrderFlowSession((s) => s.windowedTapeLoading);
  const dataLoading         = useOrderFlowSession((s) => s.dataLoading);
  const dataError           = useOrderFlowSession((s) => s.dataError);

  // Windowed tape when cursor is set, else initial full tape
  const events = windowedTapeEvents ?? tape?.events ?? [];

  // Scroll to top on new events
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [events]);

  if (!callId) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-xs text-[var(--fg-faint)]">
        No call selected
      </div>
    );
  }

  if (dataLoading) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-xs text-[var(--fg-faint)]">
        Loading…
      </div>
    );
  }

  if (dataError) {
    return (
      <div className="p-3 font-mono text-xs text-[var(--c-red)]">{dataError}</div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header strip ──────────────────────────────────────────────────── */}
      <div
        className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-3 py-1.5"
        style={{ background: "var(--bg-inset)" }}
      >
        <span className="font-mono text-[9px] uppercase tracking-widest text-[var(--fg-faint)]">
          Time &amp; Sales
          {cursorMs != null && " · ±2 min"}
        </span>
        {windowedTapeLoading && (
          <Loader2 className="size-3 animate-spin text-[var(--fg-faint)]" />
        )}
        <span className="font-mono text-[10px] text-[var(--fg-dim)]">
          {events.length.toLocaleString()} events
        </span>
      </div>

      {/* ── Column headers ────────────────────────────────────────────────── */}
      <div className="grid shrink-0 grid-cols-[7rem_3.5rem_2.5rem_4.5rem_3.5rem] gap-x-2 border-b border-[var(--border)] px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-[var(--fg-faint)]">
        <span>Time (ET)</span>
        <span>Action</span>
        <span>Side</span>
        <span className="text-right">Price</span>
        <span className="text-right">Size</span>
      </div>

      {/* ── Tape rows ─────────────────────────────────────────────────────── */}
      {events.length === 0 && !windowedTapeLoading ? (
        <div className="flex flex-1 items-center justify-center font-mono text-xs text-[var(--fg-faint)]">
          No tape events in this window
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto font-mono text-[11px]"
        >
          {events.slice(0, 500).map((e, i) => {
            const isBid    = e.side === "B";
            const isAsk    = e.side === "A";
            const isTrade  = e.action === "T" || e.action === "F";
            const sideLabel = isBid ? "Bid" : isAsk ? "Ask" : "—";

            return (
              <div
                key={`${e.t}-${e.order_id}-${i}`}
                className={[
                  "grid grid-cols-[7rem_3.5rem_2.5rem_4.5rem_3.5rem] gap-x-2 px-2 py-[3px] transition-colors hover:bg-[var(--bg-hover)]",
                  i % 2 === 0 ? "bg-[var(--panel)]/20" : "",
                  isTrade ? "font-medium" : "",
                ].join(" ")}
              >
                <span
                  className="tabular-nums text-[var(--fg-dim)]"
                  data-testid="tape-event-time"
                >
                  {formatTimeMsET(e.t)}
                </span>
                <span className="text-[var(--fg-faint)]">
                  {ACTION_LABELS[e.action] ?? e.action}
                </span>
                <span
                  style={{
                    color: isBid
                      ? "var(--c-green)"
                      : isAsk
                        ? "var(--c-red)"
                        : "var(--fg-faint)",
                  }}
                >
                  {sideLabel}
                </span>
                <span className="text-right tabular-nums text-[var(--foreground)]">
                  {e.price.toFixed(2)}
                </span>
                <span className="text-right tabular-nums text-[var(--fg-dim)]">
                  {e.size.toLocaleString()}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
