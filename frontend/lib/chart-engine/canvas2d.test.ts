/**
 * Bar-column geometry invariants. These are the rules that were violated by
 * snapping the candle body (Math.round per edge) and the candle wick
 * (Math.floor + 0.5) independently: wicks leaned left on one candle and right
 * on the next, and body widths alternated 3px/4px inside a single frame.
 *
 * Everything here is measured in DEVICE pixels — that is the grid the artifacts
 * live on. barColumn touches no canvas context, so a stub ctx is enough.
 */
import { describe, expect, it } from "vitest";

import { Canvas2DPainter } from "./canvas2d";
import { MAX_BAR_SPACING, MIN_BAR_SPACING } from "./types";

function painterAt(dpr: number): Canvas2DPainter {
  const p = new Canvas2DPainter({} as CanvasRenderingContext2D);
  p.setSize(600, 400, dpr);
  return p;
}

/** Bar centres for a run of bars — deliberately fractional, as xOf produces. */
function centres(spacing: number, count = 40): number[] {
  const xs: number[] = [];
  for (let i = 0; i < count; i++) xs.push(549.328 - (40.7 - i) * spacing);
  return xs;
}

/** Every spacing the viewport can actually reach, at 0.05px resolution. */
function spacings(): number[] {
  const out: number[] = [];
  for (let s = MIN_BAR_SPACING; s <= MAX_BAR_SPACING; s += 0.05) {
    out.push(Number(s.toFixed(2)));
  }
  return out;
}

describe.each([1, 2, 1.5])("barColumn at dpr=%s", (dpr) => {
  const p = painterAt(dpr);
  const dev = (v: number) => v * dpr;

  it("centres the wick inside the body — no lean, ever", () => {
    for (const s of spacings()) {
      for (const x of centres(s)) {
        const c = p.barColumn(x, s);
        const bodyCentre = dev(c.bodyX) + dev(c.bodyW) / 2;
        const wickCentre = dev(c.wickX) + dev(c.wickW) / 2;
        expect(Math.abs(bodyCentre - wickCentre)).toBeLessThan(1e-9);
      }
    }
  });

  it("lands every edge on a whole device pixel", () => {
    for (const s of spacings()) {
      for (const x of centres(s)) {
        const c = p.barColumn(x, s);
        for (const edge of [
          dev(c.bodyX),
          dev(c.bodyX) + dev(c.bodyW),
          dev(c.wickX),
          dev(c.wickX) + dev(c.wickW),
        ]) {
          expect(Math.abs(edge - Math.round(edge))).toBeLessThan(1e-9);
        }
      }
    }
  });

  it("uses ONE body width per frame", () => {
    for (const s of spacings()) {
      const widths = new Set(
        centres(s).map((x) => Math.round(dev(p.barColumn(x, s).bodyW))),
      );
      expect(widths.size).toBe(1);
    }
  });

  it("never lets adjacent bodies touch", () => {
    for (const s of spacings()) {
      const cols = centres(s).map((x) => p.barColumn(x, s));
      // Below ~1px per bar the slots themselves overlap and bars are drawn as
      // bare wicks — nothing to keep apart. Wherever a body DOES draw, the
      // tightest pair in the frame must still show a gap.
      if (cols[0].bodyW <= cols[0].wickW) continue;
      for (let i = 1; i < cols.length; i++) {
        const gap = dev(cols[i].bodyX) - (dev(cols[i - 1].bodyX) + dev(cols[i - 1].bodyW));
        expect(gap).toBeGreaterThan(0);
      }
    }
  });

  it("keeps the wick exactly 1 logical px thick", () => {
    const c = p.barColumn(100.3, 7);
    expect(dev(c.wickW)).toBe(Math.max(1, Math.round(dpr)));
  });
});

describe("barColumn width policy", () => {
  it("holds the default view's 5px body (no visual change at spacing 7)", () => {
    for (const dpr of [1, 2]) {
      const c = painterAt(dpr).barColumn(100.3, 7);
      expect(c.bodyW).toBe(5);
    }
  });

  it("collapses to a bare wick only when a body cannot fit", () => {
    const p = painterAt(1);
    // Wide enough for a 3px body + 1px gap → body draws.
    expect(p.barColumn(100.3, 5).bodyW).toBeGreaterThan(p.barColumn(100.3, 5).wickW);
    // A 3px body in a 3.5px slot would merge the candles, so: wick only.
    expect(p.barColumn(100.3, 3.5).bodyW).toBe(p.barColumn(100.3, 3.5).wickW);
  });
});
