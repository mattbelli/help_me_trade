import { describe, expect, it } from "vitest";

import { crosshairTimeLabel, decimalsFor, niceStep, priceTicks, timeTicks } from "./ticks";
import { defaultViewport } from "./scales";
import { PRICE_TICK_TARGET_PX, TIME_TICK_TARGET_PX, type EngineBar } from "./types";

const W = 800;

function bar(timeMs: number): EngineBar {
  return { time: timeMs, open: 1, high: 2, low: 0.5, close: 1.5, volume: 100 };
}

/** 5-min intraday bars starting at an ET wall-clock instant (July = UTC−4). */
function intradayBars(startIsoUtc: string, count: number, stepMin = 5): EngineBar[] {
  const start = Date.parse(startIsoUtc);
  return Array.from({ length: count }, (_, i) => bar(start + i * stepMin * 60_000));
}

describe("niceStep", () => {
  it("returns the smallest 1-2-5 step ≥ raw", () => {
    expect(niceStep(0.3)).toBeCloseTo(0.5, 12);
    expect(niceStep(3)).toBe(5);
    expect(niceStep(12)).toBe(20);
    expect(niceStep(0.011)).toBeCloseTo(0.02, 12);
    expect(niceStep(1)).toBe(1);
    expect(niceStep(50)).toBe(50);
    expect(niceStep(51)).toBe(100);
  });
});

describe("decimalsFor", () => {
  it("keeps at least 2 decimals, more for sub-cent steps", () => {
    expect(decimalsFor(5)).toBe(2);
    expect(decimalsFor(0.5)).toBe(2);
    expect(decimalsFor(0.001)).toBe(3);
    expect(decimalsFor(0.0005)).toBe(4);
  });
});

describe("priceTicks", () => {
  it("emits sorted in-range multiples of a 1-2-5 step at roughly the target density", () => {
    const ticks = priceTicks({ min: 92.3, max: 117.9 }, 400);
    expect(ticks.length).toBeGreaterThan(2);
    expect(ticks.length).toBeLessThanOrEqual(Math.ceil(400 / PRICE_TICK_TARGET_PX) + 1);
    for (const t of ticks) {
      expect(t.price).toBeGreaterThanOrEqual(92.3);
      expect(t.price).toBeLessThanOrEqual(117.9);
    }
    for (let i = 1; i < ticks.length; i++) {
      expect(ticks[i].price).toBeGreaterThan(ticks[i - 1].price);
    }
    // step-derived labels: uniform decimals
    const decimals = new Set(ticks.map((t) => t.label.split(".")[1]?.length ?? 0));
    expect(decimals.size).toBe(1);
  });

  it("is empty on degenerate ranges", () => {
    expect(priceTicks({ min: 5, max: 5 }, 400)).toEqual([]);
    expect(priceTicks({ min: 5, max: 10 }, 0)).toEqual([]);
  });
});

describe("timeTicks", () => {
  it("prefers the ET day boundary over plain times and keeps ticks spaced apart", () => {
    // Two sessions of 5-min bars: Jul 8 09:30–16:00 ET and Jul 9 09:30–12:00 ET.
    const day1 = intradayBars("2026-07-08T13:30:00Z", 78);
    const day2 = intradayBars("2026-07-09T13:30:00Z", 30);
    const bars = [...day1, ...day2];
    const vp = defaultViewport(bars.length, W);
    const ticks = timeTicks(bars, vp, W, true);

    expect(ticks.length).toBeGreaterThan(2);
    // The first bar of day 2 must carry a date label, not a time.
    const boundary = ticks.find((t) => t.index === day1.length);
    expect(boundary?.label).toBe("Jul 9");
    expect(boundary?.major).toBe(true);
    // Non-boundary ticks label ET times.
    const timeTick = ticks.find((t) => !t.major);
    expect(timeTick?.label).toMatch(/^\d{1,2}:\d{2} (AM|PM)$/);
    // Spacing respects the target gap.
    for (let i = 1; i < ticks.length; i++) {
      expect(Math.abs(ticks[i].x - ticks[i - 1].x)).toBeGreaterThanOrEqual(
        TIME_TICK_TARGET_PX,
      );
    }
  });

  it("labels month and year boundaries on daily bars", () => {
    // Daily bars (weekdays ignored — synthetic) spanning Dec 2025 → Feb 2026.
    const start = Date.parse("2025-12-15T21:00:00Z"); // 16:00 ET close-ish stamp
    const bars = Array.from({ length: 60 }, (_, i) => bar(start + i * 86_400_000));
    const vp = defaultViewport(bars.length, W);
    const ticks = timeTicks(bars, vp, W, false);
    const labels = ticks.map((t) => t.label);
    expect(labels).toContain("2026"); // year boundary wins over "Jan"
    expect(labels).toContain("Feb");
  });

  it("returns empty for empty data", () => {
    expect(timeTicks([], defaultViewport(0, W), W, true)).toEqual([]);
  });
});

describe("crosshairTimeLabel", () => {
  it("formats ET wall-clock for intraday and dates for daily", () => {
    const t = Date.parse("2026-07-09T13:30:00Z"); // 09:30 ET
    expect(crosshairTimeLabel(t, true)).toMatch(/Jul 9.*9:30 AM/);
    expect(crosshairTimeLabel(t, false)).toMatch(/Jul 9, 2026/);
  });
});
