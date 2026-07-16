"use client";

/**
 * T0 latency instrumentation (client half). Lightweight `performance.mark`
 * wrappers + a tiny store so the terminal can render a dev-visible readout
 * ("⏱ summon 78ms", "⏱ echo 4ms") without shipping cost to production.
 *
 * Enable with `localStorage.hmt-perf-debug = "1"` or `?perf=1`, or the
 * `NEXT_PUBLIC_PERF_DEBUG=1` build flag. No-ops otherwise.
 */

import { create } from "zustand";

export type PerfReading = { label: string; ms: number; at: number };

type PerfState = {
  enabled: boolean;
  last: PerfReading | null;
  setEnabled: (on: boolean) => void;
  record: (reading: PerfReading) => void;
};

export const usePerfStore = create<PerfState>((set) => ({
  enabled: false,
  last: null,
  setEnabled: (on) => set({ enabled: on }),
  record: (reading) => set({ last: reading }),
}));

export function isPerfDebug(): boolean {
  if (typeof window === "undefined") {
    return process.env.NEXT_PUBLIC_PERF_DEBUG === "1";
  }
  try {
    if (localStorage.getItem("hmt-perf-debug") === "1") return true;
    if (new URLSearchParams(window.location.search).get("perf") === "1")
      return true;
  } catch {
    // ignore
  }
  return process.env.NEXT_PUBLIC_PERF_DEBUG === "1";
}

/**
 * Time the work between now and the next animation frame (i.e. paint), then
 * publish it as the latest reading. Used for "summon" (drawer open → first
 * painted frame) and "echo" (keypress → echoed line painted).
 */
export function markToPaint(label: string): void {
  if (!usePerfStore.getState().enabled) return;
  const t0 = performance.now();
  requestAnimationFrame(() => {
    const ms = performance.now() - t0;
    usePerfStore.getState().record({ label, ms, at: Date.now() });
    console.log(`[perf] ${label} ${ms.toFixed(1)}ms`);
  });
}
