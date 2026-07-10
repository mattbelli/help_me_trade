import { describe, expect, it } from "vitest";

import { yOf } from "./scales";
import { computeLayout, hitTestLine, LINE_HIT_PX, type Scene } from "./series";
import type { OverlayLine } from "./types";

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
