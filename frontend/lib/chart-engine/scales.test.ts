import { describe, expect, it } from "vitest";

import {
  autoPriceRange,
  clampViewport,
  defaultViewport,
  indexAt,
  pan,
  priceAt,
  visibleBarRange,
  xOf,
  yOf,
  zoomAt,
} from "./scales";
import {
  AUTOSCALE_PAD,
  DEFAULT_BAR_SPACING,
  DEFAULT_RIGHT_OFFSET,
  MAX_BAR_SPACING,
  MIN_BAR_SPACING,
  MIN_VISIBLE_BARS,
  type EngineBar,
  type Viewport,
} from "./types";

const W = 800;

function mkBars(n: number, base = 100): EngineBar[] {
  return Array.from({ length: n }, (_, i) => ({
    time: 1_700_000_000_000 + i * 300_000,
    open: base + i,
    high: base + i + 2,
    low: base + i - 2,
    close: base + i + 1,
    volume: 1000 + i,
  }));
}

describe("bar index ↔ x mapping", () => {
  const vp: Viewport = { barSpacing: 7, rightEdgeIndex: 104 };

  it("is a exact inverse pair", () => {
    for (const x of [0, 1, 123.4, 400, W - 1, W]) {
      expect(xOf(indexAt(x, vp, W), vp, W)).toBeCloseTo(x, 9);
    }
    for (const i of [-3, 0, 50.5, 104]) {
      expect(indexAt(xOf(i, vp, W), vp, W)).toBeCloseTo(i, 9);
    }
  });

  it("puts rightEdgeIndex at the pane's right edge", () => {
    expect(xOf(vp.rightEdgeIndex, vp, W)).toBe(W);
  });
});

describe("zoomAt", () => {
  it("keeps the bar under the cursor fixed (anchor invariance)", () => {
    const vp: Viewport = { barSpacing: 7, rightEdgeIndex: 300 };
    for (const x of [100, 400, 750]) {
      for (const factor of [1.25, 0.8, 2, 0.5]) {
        const anchor = indexAt(x, vp, W);
        const next = zoomAt(vp, x, factor, W, 1000);
        expect(indexAt(x, next, W)).toBeCloseTo(anchor, 9);
      }
    }
  });

  it("clamps barSpacing to its bounds", () => {
    const vp: Viewport = { barSpacing: 7, rightEdgeIndex: 300 };
    expect(zoomAt(vp, 400, 1e6, W, 1000).barSpacing).toBe(MAX_BAR_SPACING);
    expect(zoomAt(vp, 400, 1e-6, W, 1000).barSpacing).toBe(MIN_BAR_SPACING);
  });
});

describe("pan + clamps", () => {
  it("shifts rightEdgeIndex by dx/barSpacing", () => {
    const vp: Viewport = { barSpacing: 10, rightEdgeIndex: 500 };
    // Drag right 100px → view older data → right edge index decreases by 10.
    expect(pan(vp, 100, W, 1000).rightEdgeIndex).toBeCloseTo(490, 9);
    expect(pan(vp, -50, W, 1000).rightEdgeIndex).toBeCloseTo(505, 9);
  });

  it("caps whitespace right of the last bar at paneWidth/barSpacing − MIN_VISIBLE_BARS", () => {
    const vp: Viewport = { barSpacing: 10, rightEdgeIndex: 500 };
    const n = 300;
    const out = pan(vp, -1e9, W, n); // fling left → far future
    expect(out.rightEdgeIndex).toBeCloseTo(n - 1 + W / 10 - MIN_VISIBLE_BARS, 9);
  });

  it("never scrolls the first bars off the right edge", () => {
    const vp: Viewport = { barSpacing: 10, rightEdgeIndex: 500 };
    const out = pan(vp, 1e9, W, 300); // fling right → far past
    expect(out.rightEdgeIndex).toBe(MIN_VISIBLE_BARS - 1);
  });

  it("clampViewport handles empty data without NaN", () => {
    const out = clampViewport({ barSpacing: 7, rightEdgeIndex: 42 }, W, 0);
    expect(out.rightEdgeIndex).toBe(0);
    expect(out.barSpacing).toBe(7);
  });
});

describe("defaultViewport", () => {
  it("anchors the last bar with the default right offset", () => {
    const out = defaultViewport(500, W);
    expect(out.barSpacing).toBe(DEFAULT_BAR_SPACING);
    expect(out.rightEdgeIndex).toBe(499 + DEFAULT_RIGHT_OFFSET);
  });

  it("clamps for tiny datasets", () => {
    const out = defaultViewport(1, W);
    expect(out.rightEdgeIndex).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(out.rightEdgeIndex)).toBe(true);
  });
});

describe("visibleBarRange", () => {
  it("returns integer indices clamped to the data", () => {
    const vp: Viewport = { barSpacing: 8, rightEdgeIndex: 104 };
    const range = visibleBarRange(vp, W, 1000);
    expect(range).not.toBeNull();
    expect(range!.from).toBeGreaterThanOrEqual(0);
    expect(range!.to).toBeLessThanOrEqual(999);
    // right edge at index 104 → bar 104 visible, bar 105 not
    expect(range!.to).toBe(104);
    expect(Number.isInteger(range!.from)).toBe(true);
  });

  it("is null when the window sits entirely in right whitespace or data is empty", () => {
    expect(visibleBarRange({ barSpacing: 8, rightEdgeIndex: 104 }, W, 0)).toBeNull();
    // 100px pane, right edge far right of the last (and only) bars
    expect(
      visibleBarRange({ barSpacing: 10, rightEdgeIndex: 500 }, 100, 10),
    ).toBeNull();
  });
});

describe("autoPriceRange", () => {
  it("pads the visible high/low by AUTOSCALE_PAD", () => {
    const bars = mkBars(100);
    const vp = defaultViewport(100, W); // everything visible
    const r = autoPriceRange(bars, vp, W, "candle");
    const lo = bars[0].low; // 98
    const hi = bars[99].high; // 201
    const pad = (hi - lo) * AUTOSCALE_PAD;
    expect(r.min).toBeCloseTo(lo - pad, 9);
    expect(r.max).toBeCloseTo(hi + pad, 9);
  });

  it("uses closes only for line style", () => {
    const bars = mkBars(50);
    const vp = defaultViewport(50, W);
    const r = autoPriceRange(bars, vp, W, "line");
    const pad = (bars[49].close - bars[0].close) * AUTOSCALE_PAD;
    expect(r.min).toBeCloseTo(bars[0].close - pad, 9); // lows/highs never enter
    expect(r.max).toBeCloseTo(bars[49].close + pad, 9);
  });

  it("never collapses on flat data", () => {
    const bars: EngineBar[] = mkBars(10).map((b) => ({
      ...b,
      open: 50,
      high: 50,
      low: 50,
      close: 50,
    }));
    const r = autoPriceRange(bars, defaultViewport(10, W), W, "candle");
    expect(r.max).toBeGreaterThan(r.min);
  });

  it("only considers visible bars", () => {
    const bars = mkBars(1000);
    // Zoomed into the tail: bars ~897+ visible at spacing 8.
    const vp: Viewport = { barSpacing: 8, rightEdgeIndex: 999 };
    const r = autoPriceRange(bars, vp, W, "candle");
    expect(r.min).toBeGreaterThan(bars[0].low + 100); // early lows excluded
  });
});

describe("yOf / priceAt", () => {
  const range = { min: 90, max: 110 };

  it("maps range extremes to rect edges and inverts", () => {
    expect(yOf(110, range, 10, 200)).toBe(10);
    expect(yOf(90, range, 10, 200)).toBe(210);
    for (const y of [10, 55.5, 123, 210]) {
      expect(yOf(priceAt(y, range, 10, 200), range, 10, 200)).toBeCloseTo(y, 9);
    }
  });
});
