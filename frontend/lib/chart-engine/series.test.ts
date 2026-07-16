import { describe, expect, it } from "vitest";

import { indexOfTime, xOf, yOf } from "./scales";
import {
  computeLayout,
  hitTestLine,
  hitTestSegment,
  LINE_HIT_PX,
  type Scene,
} from "./series";
import type { EngineBar, OverlayLine, OverlaySegment, Viewport } from "./types";

describe("computeLayout study panes", () => {
  it("no studies: pane runs to the time axis, no study rects", () => {
    const l = computeLayout(800, 400);
    expect(l.studyPanes).toEqual([]);
    expect(l.pane.h).toBe(l.timeAxis.y);
  });

  it("carves stacked sub-panes off the bottom, nothing overlaps", () => {
    const l = computeLayout(800, 400, 0, 2);
    expect(l.studyPanes).toHaveLength(2);
    const [a, b] = l.studyPanes;
    expect(a.y).toBe(l.pane.h);
    expect(b.y).toBe(a.y + a.h);
    expect(b.y + b.h).toBe(l.timeAxis.y);
    expect(a.h).toBeGreaterThan(0);
    // Volume strip stays inside the (shrunken) main pane.
    expect(l.volume.y + l.volume.h).toBe(l.pane.h);
    // Axis column still spans the full plot height.
    expect(l.priceAxis.h).toBe(l.timeAxis.y);
  });
});

function mkScene(): Scene {
  return {
    layout: computeLayout(800, 400),
    priceRange: { min: 90, max: 110 },
    pTicks: [],
    tTicks: [],
  };
}

function yFor(scene: Scene, price: number): number {
  const pr = scene.layout.price;
  return yOf(price, scene.priceRange, pr.y, pr.h);
}

describe("hitTestLine", () => {
  const scene = mkScene();
  const lines: OverlayLine[] = [
    { price: 100, color: "#fff", id: "alert:1", draggable: true },
    { price: 101, color: "#fff", id: "dlevel:2" }, // hittable, not draggable
    { price: 99, color: "#fff" }, // anonymous — never hittable
  ];

  it("returns the nearest identifiable line within tolerance", () => {
    const y = yFor(scene, 100) + 2;
    expect(hitTestLine(lines, y, scene, LINE_HIT_PX, false)?.id).toBe("alert:1");
  });

  it("draggableOnly skips hittable-but-fixed lines", () => {
    const y = yFor(scene, 101);
    expect(hitTestLine(lines, y, scene, LINE_HIT_PX, false)?.id).toBe("dlevel:2");
    // Nearest draggable within tolerance wins instead (alert at 100 is
    // ~16px away here, outside the 6px tolerance → no hit).
    expect(hitTestLine(lines, y, scene, LINE_HIT_PX, true)).toBeNull();
  });

  it("anonymous lines are transparent to hits", () => {
    const y = yFor(scene, 99);
    expect(hitTestLine(lines, y, scene, LINE_HIT_PX, false)).toBeNull();
  });

  it("misses outside tolerance and outside the price area", () => {
    const y = yFor(scene, 100);
    expect(hitTestLine(lines, y + LINE_HIT_PX + 1, scene, LINE_HIT_PX, false)).toBeNull();
    const volumeY = scene.layout.volume.y + 5;
    expect(hitTestLine(lines, volumeY, scene, LINE_HIT_PX, false)).toBeNull();
  });

  it("an out-of-range (hidden) line is not hittable", () => {
    const hidden: OverlayLine[] = [
      { price: 200, color: "#fff", id: "alert:9", draggable: true },
    ];
    // Its unclamped y is far above the pane; probing the top edge finds nothing.
    expect(hitTestLine(hidden, 0, scene, LINE_HIT_PX, false)).toBeNull();
  });
});

describe("hitTestSegment", () => {
  const scene = mkScene();
  const bars: EngineBar[] = Array.from({ length: 100 }, (_, i) => ({
    time: 1_700_000_000_000 + i * 300_000,
    open: 100,
    high: 101,
    low: 99,
    close: 100,
    volume: 1,
  }));
  const vp: Viewport = { barSpacing: 7, rightEdgeIndex: 99 };
  const seg: OverlaySegment = {
    a: { time: bars[20].time, price: 95 },
    b: { time: bars[80].time, price: 105 },
    color: "#fff",
    id: "shape:1",
    draggable: true,
  };
  const pr = scene.layout.price;
  const xy = (i: number, price: number) => ({
    x: xOf(indexOfTime(bars, bars[i].time), vp, scene.layout.pane.w),
    y: yOf(price, scene.priceRange, pr.y, pr.h),
  });

  it("endpoint handles outrank the body", () => {
    const a = xy(20, 95);
    const hit = hitTestSegment([seg], a.x + 3, a.y + 3, bars, vp, scene, LINE_HIT_PX, true);
    expect(hit?.part).toBe("a");
  });

  it("hits the body between endpoints", () => {
    const a = xy(20, 95);
    const b = xy(80, 105);
    const hit = hitTestSegment(
      [seg],
      (a.x + b.x) / 2,
      (a.y + b.y) / 2,
      bars,
      vp,
      scene,
      LINE_HIT_PX,
      false,
    );
    expect(hit?.part).toBe("body");
  });

  it("channel width handle sits at the parallel-leg midpoint", () => {
    const chan: OverlaySegment = { ...seg, parallelOffset: 4, id: "shape:2" };
    const a = xy(20, 95 + 4);
    const b = xy(80, 105 + 4);
    const hit = hitTestSegment(
      [chan],
      (a.x + b.x) / 2,
      (a.y + b.y) / 2,
      bars,
      vp,
      scene,
      LINE_HIT_PX,
      true,
    );
    expect(hit?.part).toBe("width");
  });

  it("a zero-width channel keeps its width handle (recovery path)", () => {
    const flat: OverlaySegment = { ...seg, parallelOffset: 0, id: "shape:3" };
    const a = xy(20, 95);
    const b = xy(80, 105);
    const hit = hitTestSegment(
      [flat],
      (a.x + b.x) / 2,
      (a.y + b.y) / 2,
      bars,
      vp,
      scene,
      LINE_HIT_PX,
      true,
    );
    expect(hit?.part).toBe("width");
  });

  it("misses far away and outside the price area", () => {
    const a = xy(20, 95);
    expect(
      hitTestSegment([seg], a.x, a.y + 40, bars, vp, scene, LINE_HIT_PX, false),
    ).toBeNull();
    expect(
      hitTestSegment(
        [seg],
        a.x,
        scene.layout.volume.y + 5,
        bars,
        vp,
        scene,
        LINE_HIT_PX,
        false,
      ),
    ).toBeNull();
  });
});
