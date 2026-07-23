import { describe, expect, it } from "vitest";

import {
  parsePersisted,
  selectPersistable,
  type StoredPair,
} from "@/lib/bars-cache-persist";

const pair = (key: string, fetchedAt: number): StoredPair => [
  key,
  { data: { bars: ["x".repeat(10)] }, fetchedAt },
];

const sizeOf = (p: StoredPair) => p[0].length + JSON.stringify(p[1]).length + 4;

describe("selectPersistable", () => {
  it("keeps newest-fetched first and drops the oldest tail past the byte budget", () => {
    const a = pair("a", 1); // oldest
    const b = pair("b", 3); // newest
    const c = pair("c", 2);
    const budget = sizeOf(a) + sizeOf(b) + sizeOf(c) - 1; // one byte short of all three
    const kept = selectPersistable([a, b, c], budget);
    expect(kept.map((k) => k[0])).toEqual(["b", "c"]); // newest two; oldest (a) dropped
  });

  it("returns everything when the budget is ample", () => {
    expect(selectPersistable([pair("a", 1), pair("b", 2)], 1_000_000)).toHaveLength(2);
  });

  it("persists nothing when even the newest entry exceeds the budget", () => {
    expect(selectPersistable([pair("a", 1)], 1)).toEqual([]);
  });
});

describe("parsePersisted", () => {
  it("round-trips a serialized selection", () => {
    const kept = selectPersistable([pair("a", 1), pair("b", 2)], 1_000_000);
    const back = parsePersisted(JSON.stringify(kept));
    expect(back).toHaveLength(2);
    expect(back.map((k) => k[0]).sort()).toEqual(["a", "b"]);
  });

  it("tolerates null / non-JSON / wrong-shape / incomplete entries", () => {
    expect(parsePersisted(null)).toEqual([]);
    expect(parsePersisted("not json")).toEqual([]);
    expect(parsePersisted(JSON.stringify({ not: "an array" }))).toEqual([]);
    // entry missing fetchedAt, and entry missing data — both filtered out
    expect(parsePersisted(JSON.stringify([["k", { data: 1 }]]))).toEqual([]);
    expect(parsePersisted(JSON.stringify([["k", { fetchedAt: 5 }]]))).toEqual([]);
  });

  it("rejects entries whose data is not a bars response (bars must be an array)", () => {
    // A stale-schema blob whose data survived JSON round-trip but isn't a
    // BarsResponse would crash the chart at data.bars.map — must be dropped here.
    for (const data of [42, "bars", {}, { bars: "not-an-array" }, { bars: {} }]) {
      expect(
        parsePersisted(JSON.stringify([["k", { data, fetchedAt: 5 }]])),
      ).toEqual([]);
    }
    const good: StoredPair = ["k", { data: { bars: [] }, fetchedAt: 5 }];
    expect(parsePersisted(JSON.stringify([good]))).toHaveLength(1);
  });
});
