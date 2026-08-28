import { describe, expect, it } from "vitest";

import {
  fullSort,
  ordersEqual,
  reconcileOrder,
  stickySort,
  type SortState,
} from "./watchlist-sort";

const DESC: SortState = { key: "change_pct", dir: "desc" };
const ASC: SortState = { key: "change_pct", dir: "asc" };

function getter(map: Record<string, number | null>) {
  return (sym: string) => map[sym] ?? null;
}

describe("fullSort", () => {
  it("ranks by value, nulls last", () => {
    const vals = { A: 1.2, B: 3.4, C: null, D: 0.5 };
    expect(fullSort(["A", "B", "C", "D"], getter(vals), DESC)).toEqual([
      "B",
      "A",
      "D",
      "C",
    ]);
    expect(fullSort(["A", "B", "C", "D"], getter(vals), ASC)).toEqual([
      "D",
      "A",
      "B",
      "C",
    ]);
  });

  it("sorts symbols lexicographically", () => {
    const sort: SortState = { key: "symbol", dir: "asc" };
    expect(fullSort(["MSFT", "AAPL", "TSLA"], (s) => s, sort)).toEqual([
      "AAPL",
      "MSFT",
      "TSLA",
    ]);
  });
});

describe("stickySort", () => {
  it("does not swap names that still print as the same Δ%", () => {
    // Both display as +1.23% (2-decimal). Raw values straddle each other
    // the way a live tape does — fullSort would ping-pong every tick.
    const order = ["AAPL", "MSFT"];
    const tick1 = { AAPL: 1.234, MSFT: 1.231 };
    const tick2 = { AAPL: 1.229, MSFT: 1.233 };
    expect(stickySort(order, getter(tick1), DESC)).toEqual(order);
    expect(stickySort(order, getter(tick2), DESC)).toEqual(order);
    expect(fullSort(order, getter(tick2), DESC)).toEqual(["MSFT", "AAPL"]);
  });

  it("lets a challenger take the seat once it beats by a full display unit", () => {
    const order = ["AAPL", "MSFT"];
    // 1.26 vs 1.23 — MSFT has jumped three printed ticks.
    const overtake = { AAPL: 1.23, MSFT: 1.26 };
    expect(stickySort(order, getter(overtake), DESC)).toEqual(["MSFT", "AAPL"]);
  });

  it("does not swap back until the previous occupant recrosses by a unit", () => {
    const seated = ["MSFT", "AAPL"];
    expect(stickySort(seated, getter({ MSFT: 1.26, AAPL: 1.24 }), DESC)).toEqual(
      seated,
    );
    expect(stickySort(seated, getter({ MSFT: 1.26, AAPL: 1.27 }), DESC)).toEqual([
      "AAPL",
      "MSFT",
    ]);
  });

  it("cascades a real mover through several seats in one pass", () => {
    const order = ["A", "B", "C", "D"];
    const vals = { A: 1.0, B: 0.9, C: 0.8, D: 1.5 };
    expect(stickySort(order, getter(vals), DESC)).toEqual(["D", "A", "B", "C"]);
  });

  it("sinks a name that lost its quote without waiting for hysteresis", () => {
    const order = ["AAPL", "MSFT"];
    expect(stickySort(order, getter({ AAPL: null, MSFT: 1.2 }), DESC)).toEqual([
      "MSFT",
      "AAPL",
    ]);
  });
});

describe("reconcileOrder / ordersEqual", () => {
  it("drops removed symbols and prepends new ones", () => {
    // Kept symbols stay in their previous relative order (A before C);
    // D is new so it prepends.
    expect(reconcileOrder(["A", "B", "C"], ["C", "D", "A"])).toEqual([
      "D",
      "A",
      "C",
    ]);
  });

  it("compares order identity", () => {
    expect(ordersEqual(["A", "B"], ["A", "B"])).toBe(true);
    expect(ordersEqual(["A", "B"], ["B", "A"])).toBe(false);
    expect(ordersEqual(["A"], ["A", "B"])).toBe(false);
  });
});
