// Pure, dependency-free helpers for persisting the in-memory bars cache across a
// full page reload. No window / DOM / api imports on purpose, so the tricky parts
// (byte-budget eviction, corrupt-blob tolerance) run in the node vitest environment
// without jsdom. The storage/DOM glue lives in bars-cache.ts.

export type StoredEntry = { data: unknown; fetchedAt: number };
export type StoredPair = [string, StoredEntry];

// Bumped when the persisted shape changes, so an old blob is ignored not mis-read.
export const BARS_CACHE_STORAGE_KEY = "hmt.bars-cache.v1";
// Stay well under the ~5MB sessionStorage quota — an intraday payload can be ~1MB,
// so a few charts fit; the oldest are dropped past this budget.
export const BARS_CACHE_PERSIST_BUDGET_BYTES = 3_500_000;

/**
 * Pick the entries to persist: newest-fetched first, accumulating until the byte
 * budget would be exceeded (the oldest tail is dropped). Pure — takes the cache
 * entries, returns the subset to serialize.
 */
export function selectPersistable(
  entries: StoredPair[],
  budgetBytes: number,
): StoredPair[] {
  const byNewest = [...entries].sort((a, b) => b[1].fetchedAt - a[1].fetchedAt);
  const kept: StoredPair[] = [];
  let bytes = 0;
  for (const pair of byNewest) {
    // key + serialized value + a few bytes of array/JSON framing.
    const size = pair[0].length + JSON.stringify(pair[1]).length + 4;
    if (bytes + size > budgetBytes) break;
    kept.push(pair);
    bytes += size;
  }
  return kept;
}

/**
 * Parse a persisted blob back into cache pairs, tolerating anything malformed
 * (null, non-JSON, wrong shape, entries missing data/fetchedAt) by returning [].
 * A corrupt or stale-schema blob must never throw or poison the cache.
 */
export function parsePersisted(raw: string | null): StoredPair[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is StoredPair =>
        Array.isArray(e) &&
        e.length === 2 &&
        typeof e[0] === "string" &&
        e[1] != null &&
        typeof (e[1] as StoredEntry).fetchedAt === "number" &&
        (e[1] as StoredEntry).data != null,
    );
  } catch {
    return [];
  }
}
