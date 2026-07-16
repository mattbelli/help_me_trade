"use client";

/**
 * orderflow-dom-widget.tsx
 *
 * Workspace widget: DOM (Depth of Market) Ladder.
 *
 * Reads callId + cursorMs + fineSnapshots + book from the session store
 * (populated by the footprint widget). Displays the best bid/ask ladder
 * at the closest available book snapshot to cursorMs.
 *
 * Styled entirely with design tokens — no hardcoded hex.
 */

import { useMemo } from "react";
import { formatTimeET } from "@/lib/orderflow-api";
import type { BookSnapshot } from "@/lib/orderflow-api";
import { useOrderFlowSession } from "@/lib/orderflow/use-orderflow-session";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

// ── Ladder row builder ────────────────────────────────────────────────────────

type LadderRow = {
  price:     number;
  bid:       number;
  ask:       number;
  isBestBid: boolean;
  isBestAsk: boolean;
};

function buildLadderRows(snap: BookSnapshot): LadderRow[] {
  const m = new Map<number, { bid: number; ask: number }>();
  for (const b of snap.bids) m.set(b.price, { bid: b.size, ask: 0 });
  for (const a of snap.asks) {
    const e = m.get(a.price);
    if (e) e.ask = a.size;
    else m.set(a.price, { bid: 0, ask: a.size });
  }
  const bestBid = snap.bids[0]?.price ?? -1;
  const bestAsk = snap.asks[0]?.price ?? Infinity;
  return [...m.keys()].sort((a, b) => b - a).map((p) => ({
    price:     p,
    bid:       m.get(p)!.bid,
    ask:       m.get(p)!.ask,
    isBestBid: p === bestBid,
    isBestAsk: p === bestAsk,
  }));
}

function closestSnap(snaps: BookSnapshot[], cursorMs: number | null): BookSnapshot | null {
  if (snaps.length === 0) return null;
  if (cursorMs == null) return snaps[Math.floor(snaps.length * 0.5)];

  // Largest t <= cursorMs ("book as of now"); snaps are sorted ascending by t.
  let lo = 0;
  let hi = snaps.length - 1;
  if (cursorMs < snaps[0].t) return snaps[0];

  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (snaps[mid].t <= cursorMs) lo = mid;
    else hi = mid - 1;
  }
  return snaps[lo];
}

// ── Main widget ────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function OrderflowDomWidget(_props: WidgetProps) {
  const callId          = useOrderFlowSession((s) => s.callId);
  const cursorMs        = useOrderFlowSession((s) => s.cursorMs);
  const book            = useOrderFlowSession((s) => s.book);
  const fineSnapshots   = useOrderFlowSession((s) => s.fineSnapshots);
  const fineBookLoading = useOrderFlowSession((s) => s.fineBookLoading);
  const dataLoading     = useOrderFlowSession((s) => s.dataLoading);
  const dataError       = useOrderFlowSession((s) => s.dataError);

  // Prefer fine (1s) snapshots; fall back to coarse book snapshots
  const allSnaps: BookSnapshot[] = fineSnapshots.length > 0
    ? fineSnapshots
    : (book?.snapshots ?? []);

  const resLabel = fineSnapshots.length > 0 ? "1s" : "30s";

  const snap = useMemo(
    () => closestSnap(allSnaps, cursorMs),
    [allSnaps, cursorMs],
  );

  const rows = useMemo(() => (snap ? buildLadderRows(snap) : []), [snap]);

  const maxSize = useMemo(
    () => Math.max(...rows.flatMap((r) => [r.bid, r.ask]), 1),
    [rows],
  );

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

  if (allSnaps.length === 0 && !fineBookLoading) {
    return (
      <div className="p-3 font-mono text-xs text-[var(--fg-faint)]">
        No book data for this call.
      </div>
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
          DOM · {resLabel}
          {fineBookLoading && (
            <span className="ml-2 text-[var(--c-blue)]"> loading 1s…</span>
          )}
        </span>
        <span
          className="font-mono text-[10px] text-[var(--fg-dim)]"
          data-testid="ladder-snapshot-time"
        >
          {formatTimeET(snap?.t)} ET
        </span>
      </div>

      {/* ── Column headers ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-[1fr_auto_1fr] shrink-0 border-b border-[var(--border)] px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-[var(--fg-faint)]">
        <span className="text-right">Bid</span>
        <span className="px-4 text-center">Price</span>
        <span>Ask</span>
      </div>

      {/* ── Ladder rows ───────────────────────────────────────────────────── */}
      <div className="min-h-0 flex-1 overflow-y-auto font-mono text-[11px]">
        {rows.map((row) => (
          <div
            key={row.price}
            className="grid grid-cols-[1fr_auto_1fr] transition-colors"
            style={{
              background: row.isBestBid
                ? "color-mix(in srgb, var(--c-green) 12%, transparent)"
                : row.isBestAsk
                  ? "color-mix(in srgb, var(--c-red) 10%, transparent)"
                  : undefined,
            }}
          >
            {/* Bid side */}
            <div className="relative flex items-center justify-end py-[3px] pr-2">
              {row.bid > 0 && (
                <>
                  <div
                    className="absolute inset-y-0 right-0"
                    style={{
                      width: `${(row.bid / maxSize) * 100}%`,
                      background: "color-mix(in srgb, var(--c-green) 20%, transparent)",
                    }}
                  />
                  <span
                    className="relative z-10 tabular-nums"
                    style={{
                      color: row.isBestBid ? "var(--c-green)" : "var(--fg-dim)",
                      fontWeight: row.isBestBid ? 600 : undefined,
                    }}
                  >
                    {row.bid.toLocaleString()}
                  </span>
                </>
              )}
            </div>

            {/* Price */}
            <div
              className="border-x border-[var(--border)]/30 px-3 py-[3px] text-center tabular-nums"
              style={{
                color: row.isBestBid || row.isBestAsk
                  ? "var(--foreground)"
                  : "var(--fg-faint)",
                fontWeight: row.isBestBid || row.isBestAsk ? 600 : undefined,
              }}
            >
              {row.price.toFixed(2)}
            </div>

            {/* Ask side */}
            <div className="relative flex items-center py-[3px] pl-2">
              {row.ask > 0 && (
                <>
                  <div
                    className="absolute inset-y-0 left-0"
                    style={{
                      width: `${(row.ask / maxSize) * 100}%`,
                      background: "color-mix(in srgb, var(--c-red) 18%, transparent)",
                    }}
                  />
                  <span
                    className="relative z-10 tabular-nums"
                    style={{
                      color: row.isBestAsk ? "var(--c-red)" : "var(--fg-dim)",
                      fontWeight: row.isBestAsk ? 600 : undefined,
                    }}
                  >
                    {row.ask.toLocaleString()}
                  </span>
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* ── Footer ────────────────────────────────────────────────────────── */}
      <div
        className="shrink-0 border-t border-[var(--border)] px-3 py-1 text-right font-mono text-[9px] text-[var(--fg-faint)]"
        style={{ background: "var(--bg-inset)" }}
      >
        {allSnaps.length.toLocaleString()} snapshots
        {fineSnapshots.length > 0 && (
          <span className="ml-2 text-[var(--c-green)]" data-testid="fine-book-ready">
            1s ✓
          </span>
        )}
      </div>
    </div>
  );
}
