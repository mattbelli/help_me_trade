/**
 * Phase-0 contract anchors: the layout/viewport constants the whole engine is
 * specced against (CHART_ENGINE.md). Real behavior tests arrive with each pure
 * module in Phase 1 — this file proves the vitest pipeline and pins the spec.
 */
import { describe, expect, it } from "vitest";

import {
  DEFAULT_BAR_SPACING,
  DEFAULT_RIGHT_OFFSET,
  MAX_BAR_SPACING,
  MIN_BAR_SPACING,
  MIN_VISIBLE_BARS,
  AUTOSCALE_PAD,
  VOLUME_FRACTION,
} from "./types";

describe("chart-engine contract constants", () => {
  it("bar spacing bounds bracket the default", () => {
    expect(MIN_BAR_SPACING).toBeLessThan(DEFAULT_BAR_SPACING);
    expect(DEFAULT_BAR_SPACING).toBeLessThan(MAX_BAR_SPACING);
  });

  it("viewport keeps content on screen", () => {
    expect(MIN_VISIBLE_BARS).toBeGreaterThanOrEqual(1);
    expect(DEFAULT_RIGHT_OFFSET).toBeGreaterThanOrEqual(0);
  });

  it("layout fractions are sane", () => {
    expect(VOLUME_FRACTION).toBeGreaterThan(0);
    expect(VOLUME_FRACTION).toBeLessThan(0.5);
    expect(AUTOSCALE_PAD).toBeGreaterThan(0);
    expect(AUTOSCALE_PAD).toBeLessThan(0.25);
  });
});
