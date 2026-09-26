import { describe, expect, it } from "vitest";

import {
  applyBarFrame,
  mergeFetchedBars,
  prependFetchedBars,
  type BarFrame,
  type VolumeMemoryRef,
} from "./merge";
import type { EngineBar } from "./types";

function mkBars(): EngineBar[] {
  return [
    { time: 1000, open: 10, high: 12, low: 9, close: 11, volume: 500 },
    { time: 2000, open: 11, high: 13, low: 10, close: 12, volume: 600 },
    { time: 3000, open: 12, high: 12.5, low: 11.5, close: 12.2, volume: 300 },
  ];
}

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

  it("an empty refresh keeps what we have; no local tail is invented", () => {
    expect(mergeFetchedBars([], local())).toEqual(local());
    expect(mergeFetchedBars(mkBars(), [])).toEqual(mkBars());
  });

  it("a newest WINDOW replaces only the span it covers — backfilled history survives", () => {
    const history = [
      { time: 500, open: 9, high: 9.5, low: 8.5, close: 9.2, volume: 100 },
      ...local(),
    ];
    const window = [
      { time: 2000, open: 11, high: 13, low: 10, close: 12.1, volume: 650 },
      { time: 3000, open: 12, high: 12.5, low: 11.5, close: 12.2, volume: 300 },
    ];
    const merged = mergeFetchedBars(window, history);
    expect(merged.map((b) => b.time)).toEqual([500, 1000, 2000, 3000, 4000]);
    expect(merged[2].close).toBe(12.1); // vendor's copy inside the window
    expect(merged[0].close).toBe(9.2); // backfilled history untouched
    expect(merged[4].close).toBe(12.5); // hub-built tail past the window kept
  });
});

describe("prependFetchedBars", () => {
  it("prepends only bars strictly older than our oldest and reports the count", () => {
    const page = [
      { time: 500, open: 9, high: 9.5, low: 8.5, close: 9.2, volume: 100 },
      { time: 800, open: 9.2, high: 10, low: 9, close: 9.9, volume: 120 },
      { time: 1000, open: 99, high: 99, low: 99, close: 99, volume: 1 }, // overlaps ours
    ];
    const { bars, added } = prependFetchedBars(page, mkBars());
    expect(added).toBe(2);
    expect(bars.map((b) => b.time)).toEqual([500, 800, 1000, 2000, 3000]);
    expect(bars[2].close).toBe(11); // ours wins on the overlap
  });

  it("nothing older → unchanged, added 0; empty local → the page", () => {
    const { bars, added } = prependFetchedBars(mkBars(), mkBars());
    expect(added).toBe(0);
    expect(bars).toEqual(mkBars());
    expect(prependFetchedBars(mkBars(), [])).toEqual({ bars: mkBars(), added: 3 });
  });
});


// ---------------------------------------------------------------------------
// applyBarFrame — the whole live contract: same time replaces, newer appends,
// older is history. No clock, no price, no plausibility.
// ---------------------------------------------------------------------------

const MIN = 60_000;
const T0 = 1_755_446_400_000; // 2026-08-17T16:00:00Z, minute + hour aligned

function frame(over: Partial<BarFrame> & { time: number }): BarFrame {
  return {
    symbol: "SPY",
    timeframe: "1Min",
    open: 100,
    high: 101,
    low: 99,
    close: 100.5,
    volume: 10,
    final: false,
    session: "regular",
    ...over,
  };
}

function minuteBars(): EngineBar[] {
  return [
    { time: T0 - 2 * MIN, open: 99, high: 99.5, low: 98.5, close: 99.2, volume: 5 },
    { time: T0 - MIN, open: 99.2, high: 99.8, low: 99, close: 99.6, volume: 7 },
    { time: T0, open: 99.6, high: 100, low: 99.4, close: 99.9, volume: 4 },
  ];
}

const opts1 = () => ({
  intraday: true,
  intervalMs: MIN,
  showExt: true,
  memory: { current: null } as VolumeMemoryRef,
});

describe("applyBarFrame — 1Min chart", () => {
  it("replaces the last bar wholesale when the frame is for it (frame is the authority)", () => {
    const bars = minuteBars();
    const r = applyBarFrame(bars, frame({ time: T0, open: 99.6, high: 100.4, low: 99.4, close: 100.3, volume: 40 }), opts1());
    expect(r).toEqual({ kind: "replaced", index: 2 });
    expect(bars).toHaveLength(3);
    expect(bars[2]).toEqual({ time: T0, open: 99.6, high: 100.4, low: 99.4, close: 100.3, volume: 40 });
  });

  it("appends the next minute opening at the FRAME's open — a real print, no fabricated continuation", () => {
    const bars = minuteBars();
    const r = applyBarFrame(bars, frame({ time: T0 + MIN, open: 100.1, high: 100.2, low: 100, close: 100.2, volume: 3 }), opts1());
    expect(r).toEqual({ kind: "appended", index: 3, skipped: 0 });
    expect(bars[3].time).toBe(T0 + MIN);
    expect(bars[3].open).toBe(100.1);
    expect(bars.slice(0, 3)).toEqual(minuteBars()); // history untouched
  });

  it("lands a frame two minutes on in ITS bucket and reports the hole", () => {
    const bars = minuteBars();
    const r = applyBarFrame(bars, frame({ time: T0 + 3 * MIN }), opts1());
    expect(r).toEqual({ kind: "appended", index: 3, skipped: 2 });
    expect(bars.map((b) => b.time)).toEqual([T0 - 2 * MIN, T0 - MIN, T0, T0 + 3 * MIN]);
  });

  it("applies an updatedBars correction to a kept minute by exact time", () => {
    const bars = minuteBars();
    const r = applyBarFrame(bars, frame({ time: T0 - MIN, high: 105, final: true }), opts1());
    expect(r).toEqual({ kind: "replaced", index: 1 });
    expect(bars[1].high).toBe(105);
    expect(bars).toHaveLength(3);
  });

  it("ignores frames older than the series holds, daily frames, and bad prices", () => {
    const bars = minuteBars();
    expect(applyBarFrame(bars, frame({ time: T0 - 10 * MIN }), opts1())).toEqual({ kind: "ignored" });
    expect(applyBarFrame(bars, frame({ time: T0, timeframe: "1Day" }), opts1())).toEqual({ kind: "ignored" });
    expect(applyBarFrame(bars, frame({ time: T0, close: 0 }), opts1())).toEqual({ kind: "ignored" });
    expect(applyBarFrame([], frame({ time: T0 }), opts1())).toEqual({ kind: "ignored" });
    expect(bars).toEqual(minuteBars());
  });

  it("hides non-regular sessions when ETH is off, shows them when on", () => {
    const off = { ...opts1(), showExt: false };
    const bars = minuteBars();
    expect(applyBarFrame(bars, frame({ time: T0 + MIN, session: "post" }), off)).toEqual({ kind: "ignored" });
    expect(applyBarFrame(bars, frame({ time: T0 + MIN, session: "post" }), opts1()).kind).toBe("appended");
  });
});

describe("applyBarFrame — 5Min chart rolls minutes up", () => {
  const FIVE = 5 * MIN;
  const opts5 = () => ({ ...opts1(), intervalMs: FIVE });
  const fiveBars = (): EngineBar[] => [
    { time: T0 - FIVE, open: 99, high: 99.9, low: 98.5, close: 99.6, volume: 50 },
    // vendor's partial forming bucket (as of its snapshot)
    { time: T0, open: 99.6, high: 100, low: 99.4, close: 99.9, volume: 40 },
  ];

  it("widens the forming bucket and takes the minute's close; volume grows by the delta only", () => {
    const bars = fiveBars();
    const o = opts5();
    // minute T0+2 first seen with 6 already traded (partly in the vendor's 40)
    applyBarFrame(bars, frame({ time: T0 + 2 * MIN, high: 100.5, low: 99.3, close: 100.4, volume: 6 }), o);
    expect(bars).toHaveLength(2);
    expect(bars[1]).toMatchObject({ open: 99.6, high: 100.5, low: 99.3, close: 100.4, volume: 40 });
    applyBarFrame(bars, frame({ time: T0 + 2 * MIN, high: 100.5, low: 99.3, close: 100.2, volume: 9 }), o);
    expect(bars[1].volume).toBe(43); // +3 since first seen
    // a NEW minute counts in full (it cannot be in the vendor's snapshot)
    applyBarFrame(bars, frame({ time: T0 + 3 * MIN, high: 100.6, low: 100.1, close: 100.3, volume: 2 }), o);
    expect(bars[1]).toMatchObject({ high: 100.6, low: 99.3, close: 100.3, volume: 45 });
  });

  it("opens the next 5-minute bucket at the minute's open, volume from scratch", () => {
    const bars = fiveBars();
    const o = opts5();
    const r = applyBarFrame(bars, frame({ time: T0 + FIVE + MIN, open: 100.2, high: 100.3, low: 100.1, close: 100.25, volume: 8 }), o);
    expect(r).toEqual({ kind: "appended", index: 2, skipped: 0 });
    expect(bars[2]).toEqual({ time: T0 + FIVE, open: 100.2, high: 100.3, low: 100.1, close: 100.25, volume: 8 });
    applyBarFrame(bars, frame({ time: T0 + FIVE + 2 * MIN, close: 100.5, high: 100.5, low: 100.2, volume: 4 }), o);
    expect(bars[2].volume).toBe(12);
    expect(bars[2].high).toBe(100.5);
  });

  it("a minute frame for an already-closed 5-minute bucket is ignored (history is the vendor's)", () => {
    const bars = fiveBars();
    expect(applyBarFrame(bars, frame({ time: T0 - FIVE + MIN, high: 500 }), opts5())).toEqual({ kind: "ignored" });
    expect(bars[0].high).toBe(99.9);
  });
});

describe("applyBarFrame — daily family", () => {
  const DAY = 86_400_000;
  const D0 = 1_755_403_200_000; // 2026-08-17T04:00:00Z (vendor daily stamp)
  const daily = (): EngineBar[] => [
    { time: D0 - 3 * DAY, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
    { time: D0, open: 1.5, high: 1.6, low: 1.4, close: 1.55, volume: 3 },
  ];
  const dframe = (over: Partial<BarFrame> & { time: number }) => frame({ timeframe: "1Day", ...over });
  const optsD = () => ({ intraday: false, intervalMs: DAY, showExt: false, memory: { current: null } as VolumeMemoryRef });

  it("1Day: replaces today by exact time and appends tomorrow", () => {
    const bars = daily();
    expect(applyBarFrame(bars, dframe({ time: D0, close: 1.7, high: 1.8, volume: 30 }), optsD())).toEqual({ kind: "replaced", index: 1 });
    expect(bars[1]).toMatchObject({ close: 1.7, high: 1.8, volume: 30 });
    expect(applyBarFrame(bars, dframe({ time: D0 + DAY, open: 1.7, close: 1.75 }), optsD()).kind).toBe("appended");
    expect(bars[2].time).toBe(D0 + DAY);
  });

  it("a minute frame widens the day the vendor opened — the Schwab lane sends no 1Day frame", () => {
    const bars = daily();
    const r = applyBarFrame(
      bars,
      frame({ time: D0 + 9 * 3_600_000, high: 1.9, low: 1.2, close: 1.85 }),
      optsD(),
    );
    expect(r).toEqual({ kind: "replaced", index: 1 });
    expect(bars).toHaveLength(2); // never a new daily bucket off a minute stamp
    expect(bars[1]).toMatchObject({ open: 1.5, high: 1.9, low: 1.2, close: 1.85 });
  });

  it("a minute frame outside the last day's span is left to the refetch", () => {
    const bars = daily();
    expect(applyBarFrame(bars, frame({ time: D0 + DAY }), optsD())).toEqual({ kind: "ignored" });
    expect(applyBarFrame(bars, frame({ time: D0 - MIN }), optsD())).toEqual({ kind: "ignored" });
    // Off-RTH prints stay out of a daily candle (showExt is false on daily+).
    expect(
      applyBarFrame(bars, frame({ time: D0 + 3_600_000, session: "post" }), optsD()),
    ).toEqual({ kind: "ignored" });
    expect(bars).toEqual(daily());
  });

  it("1Week: a daily frame inside the week widens the last bar; a new week appends", () => {
    const WEEK = 7 * DAY;
    const bars = [{ time: D0, open: 1.5, high: 1.6, low: 1.4, close: 1.55, volume: 3 }];
    const o = { ...optsD(), intervalMs: WEEK };
    applyBarFrame(bars, dframe({ time: D0 + 2 * DAY, high: 2, low: 1.3, close: 1.9, volume: 5 }), o);
    expect(bars).toHaveLength(1);
    expect(bars[0]).toMatchObject({ open: 1.5, high: 2, low: 1.3, close: 1.9 });
    const r = applyBarFrame(bars, dframe({ time: D0 + WEEK, open: 1.9, close: 2.1, high: 2.1, low: 1.9 }), o);
    expect(r).toEqual({ kind: "appended", index: 1, skipped: 0 });
    expect(bars[1].time).toBe(D0 + WEEK);
  });
});
