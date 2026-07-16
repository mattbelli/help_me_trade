import { describe, expect, it } from "vitest";

import { applyLive, rolledOverBar } from "./merge";
import type { EngineBar } from "./types";

function mkBars(): EngineBar[] {
  return [
    { time: 1000, open: 10, high: 12, low: 9, close: 11, volume: 500 },
    { time: 2000, open: 11, high: 13, low: 10, close: 12, volume: 600 },
    { time: 3000, open: 12, high: 12.5, low: 11.5, close: 12.2, volume: 300 },
  ];
}

describe("applyLive", () => {
  it("mutates only the last bar — history is untouched, nothing appends", () => {
    const bars = mkBars();
    const historySnapshot = structuredClone(bars.slice(0, -1));

    expect(applyLive(bars, 13.1)).toBe(true);

    expect(bars).toHaveLength(3);
    expect(bars.slice(0, -1)).toEqual(historySnapshot);
    const last = bars[2];
    expect(last.close).toBe(13.1);
    expect(last.high).toBe(13.1); // extended up
    expect(last.low).toBe(11.5); // unchanged
    expect(last.open).toBe(12); // never touched
    expect(last.time).toBe(3000); // never touched
    expect(last.volume).toBe(300); // never touched
  });

  it("extends the low on a down tick", () => {
    const bars = mkBars();
    applyLive(bars, 11.0);
    expect(bars[2].low).toBe(11.0);
    expect(bars[2].high).toBe(12.5);
    expect(bars[2].close).toBe(11.0);
  });

  it("returns false (no repaint) when nothing changed", () => {
    const bars = mkBars();
    expect(applyLive(bars, 12.2)).toBe(false); // same close, inside range
  });

  it("rejects empty data and garbage prices", () => {
    expect(applyLive([], 10)).toBe(false);
    const bars = mkBars();
    const before = structuredClone(bars);
    expect(applyLive(bars, NaN)).toBe(false);
    expect(applyLive(bars, 0)).toBe(false);
    expect(applyLive(bars, -5)).toBe(false);
    expect(applyLive(bars, Infinity)).toBe(false);
    expect(bars).toEqual(before);
  });
});

describe("rolledOverBar", () => {
  it("opens a flat zero-volume bar exactly one interval later", () => {
    const b = rolledOverBar(3000, 60_000, 12.4);
    expect(b).toEqual({
      time: 63_000,
      open: 12.4,
      high: 12.4,
      low: 12.4,
      close: 12.4,
      volume: 0,
    });
  });
});
