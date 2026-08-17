import { describe, expect, it } from "vitest";

import { applyLive, mergeFetchedBars, rolledOverBar } from "./merge";
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

describe("mergeFetchedBars", () => {
  const local = (): EngineBar[] => [
    ...mkBars(),
    // Rolled over locally; the vendor has not published this bucket yet.
    { time: 4000, open: 12.2, high: 12.6, low: 12.1, close: 12.5, volume: 0 },
  ];

  it("keeps a locally-rolled-over bar the payload doesn't reach", () => {
    const merged = mergeFetchedBars(mkBars(), local());
    expect(merged).toHaveLength(4);
    expect(merged[3].time).toBe(4000);
    expect(merged[3].close).toBe(12.5); // the forming candle survives the refresh
  });

  it("lets the vendor's copy win once it publishes that bucket", () => {
    const published = [
      ...mkBars(),
      { time: 4000, open: 12.2, high: 13, low: 12, close: 12.9, volume: 900 },
    ];
    const merged = mergeFetchedBars(published, local());
    expect(merged).toHaveLength(4);
    expect(merged[3].close).toBe(12.9); // vendor truth, not our estimate
    expect(merged[3].volume).toBe(900);
  });

  it("never duplicates or reorders — times stay strictly ascending", () => {
    const merged = mergeFetchedBars(mkBars(), local());
    const times = merged.map((b) => b.time);
    expect(times).toEqual([...times].sort((a, b) => a - b));
    expect(new Set(times).size).toBe(times.length);
  });

  it("drops a stranded local bar once the payload moves past it", () => {
    // Vendor skipped 4000 (no trades) and published 5000 instead.
    const ahead = [...mkBars(), { time: 5000, open: 13, high: 13, low: 13, close: 13, volume: 10 }];
    const merged = mergeFetchedBars(ahead, local());
    expect(merged.map((b) => b.time)).toEqual([1000, 2000, 3000, 5000]);
  });

  it("an empty payload clears, and no local tail is invented", () => {
    expect(mergeFetchedBars([], local())).toEqual([]);
    expect(mergeFetchedBars(mkBars(), [])).toEqual(mkBars());
  });
});

describe("rolledOverBar", () => {
  const prev: EngineBar = {
    time: 3000,
    open: 12,
    high: 12.5,
    low: 11.5,
    close: 12.2,
    volume: 300,
  };

  it("opens at the PREVIOUS close, never at the live price — no fabricated gap", () => {
    const b = rolledOverBar(prev, 60_000, 12.4);
    expect(b).toEqual({
      time: 63_000,
      open: 12.2, // prev.close, not the 12.4 tick
      high: 12.4,
      low: 12.2,
      close: 12.4,
      volume: 0,
    });
  });

  it("spans downward moves too — the tick becomes the low, not the open", () => {
    const b = rolledOverBar(prev, 60_000, 11.9);
    expect(b.open).toBe(12.2);
    expect(b.high).toBe(12.2);
    expect(b.low).toBe(11.9);
    expect(b.close).toBe(11.9);
  });

  it("lands in the bucket containing now when buckets were skipped", () => {
    const b = rolledOverBar(prev, 60_000, 12.4, 3);
    expect(b.time).toBe(3000 + 180_000); // 3 buckets on, not 1
  });

  it("opens a skipped-bucket rollover at prev.close too — vendor lag, not a gap", () => {
    // Measured 2026-08-17: on liquid names the tail is routinely 2-3 buckets
    // behind at load, so skipped buckets mean the vendor is late, not that
    // nothing traded. The candle must still start where the last one ended.
    const b = rolledOverBar(prev, 60_000, 12.4, 2);
    expect(b).toEqual({
      time: 123_000,
      open: 12.2,
      high: 12.4,
      low: 12.2,
      close: 12.4,
      volume: 0,
    });
  });

  it("treats an explicit single step exactly like the default", () => {
    expect(rolledOverBar(prev, 60_000, 12.4, 1)).toEqual(
      rolledOverBar(prev, 60_000, 12.4),
    );
  });

  it("is a flat doji when price has not moved since the previous close", () => {
    const b = rolledOverBar(prev, 60_000, 12.2);
    expect(b).toEqual({
      time: 63_000,
      open: 12.2,
      high: 12.2,
      low: 12.2,
      close: 12.2,
      volume: 0,
    });
  });
});
