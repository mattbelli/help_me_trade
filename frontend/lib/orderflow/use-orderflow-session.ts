"use client";

/**
 * use-orderflow-session.ts
 *
 * Global Zustand store that syncs the four order-flow workspace widgets:
 *   - callId         → which call is selected (footprint widget owns the selector)
 *   - cursorMs       → master timeline cursor (scrubber + playback → DOM + tape follow)
 *   - viewMs         → footprint time-view range (heatmap follows this to stay in sync)
 *   - data fields    → loaded once by the footprint widget, consumed by all four
 *
 * Pattern: footprint widget calls setCallId() → clears stale data and shows loading.
 * Footprint widget uses useOrderFlowData(callId) and writes results via setData().
 * DOM, tape, heatmap widgets read from the store — no duplicate fetches.
 */

import { create } from "zustand";
import type {
  BookData,
  BookSnapshot,
  CandleData,
  FootprintData,
  OrderFlowMeta,
  TapeData,
  TapeEvent,
  TradeEvent,
} from "@/lib/orderflow-api";

export type ViewMs = { start: number; end: number };

type OrderFlowSessionState = {
  // ── Shared call selection ─────────────────────────────────────────────────
  callId: string | null;
  // ── Master timeline cursor (ms UTC) — drives DOM + tape ──────────────────
  cursorMs: number | null;
  // ── Footprint visible time range — heatmap syncs to this ─────────────────
  viewMs: ViewMs | null;
  // ── Core data (loaded by footprint widget, consumed by all) ──────────────
  meta: OrderFlowMeta | null;
  book: BookData | null;
  candles: CandleData | null;
  footprint: FootprintData | null;
  trades: TradeEvent[];
  tape: TapeData | null;
  dataLoading: boolean;
  dataError: string | null;
  // ── Fine book (1s resolution for DOM ladder) ──────────────────────────────
  fineSnapshots: BookSnapshot[];
  fineBookLoading: boolean;
  // ── Windowed tape (±2 min around cursor) ─────────────────────────────────
  windowedTapeEvents: TapeEvent[] | null;
  windowedTapeLoading: boolean;
};

type OrderFlowSessionActions = {
  /** Select a call — clears all stale data and marks loading. */
  setCallId: (id: string | null) => void;
  /** Update the master timeline cursor. */
  setCursorMs: (ms: number | null) => void;
  /** Sync the heatmap view range from the footprint canvas. */
  setViewMs: (v: ViewMs) => void;
  /** Merge a partial state update (used by footprint widget after fetch). */
  setData: (patch: Partial<OrderFlowSessionState>) => void;
};

export type OrderFlowSession = OrderFlowSessionState & OrderFlowSessionActions;

export const useOrderFlowSession = create<OrderFlowSession>((set) => ({
  // ── Initial state ─────────────────────────────────────────────────────────
  callId: null,
  cursorMs: null,
  viewMs: null,
  meta: null,
  book: null,
  candles: null,
  footprint: null,
  trades: [],
  tape: null,
  dataLoading: false,
  dataError: null,
  fineSnapshots: [],
  fineBookLoading: false,
  windowedTapeEvents: null,
  windowedTapeLoading: false,

  // ── Actions ───────────────────────────────────────────────────────────────
  setCallId: (id) =>
    set({
      callId: id,
      cursorMs: null,
      viewMs: null,
      meta: null,
      book: null,
      candles: null,
      footprint: null,
      trades: [],
      tape: null,
      dataLoading: id != null,
      dataError: null,
      fineSnapshots: [],
      fineBookLoading: false,
      windowedTapeEvents: null,
      windowedTapeLoading: false,
    }),

  setCursorMs: (ms) => set({ cursorMs: ms }),
  setViewMs: (v) => set({ viewMs: v }),
  setData: (patch) => set(patch),
}));
