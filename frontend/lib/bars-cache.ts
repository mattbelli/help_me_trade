import { fetchBars, type BarsResponse } from "@/lib/api";
import {
  BARS_CACHE_PERSIST_BUDGET_BYTES,
  BARS_CACHE_STORAGE_KEY,
  parsePersisted,
  selectPersistable,
} from "@/lib/bars-cache-persist";

// In-memory bars cache so navigating back to a chart you just viewed reads from
// memory instead of re-hitting the network on every mount (the chart component
// unmounts on page/route switch and refetched fresh each time). The live-tick
// layer in tv-light-chart keeps the forming candle current independently, so a
// cached historical load is never stale-looking.
//
// TTLs mirror the backend _BARS_CACHE_TTL (api/main.py) — the client cache is
// therefore never staler than what the server would itself serve.
const TTL_MS: Record<string, number> = {
  "1Min": 60_000,
  "5Min": 180_000,
  "15Min": 300_000,
  "1Hour": 600_000,
  "4Hour": 600_000,
  "1Day": 300_000,
  "1Week": 300_000,
  "1Month": 300_000,
};
const DEFAULT_TTL_MS = 300_000;

// Cap entries so a long session browsing many symbols can't grow unbounded
// (an intraday response can be ~1MB). Oldest-fetched entry is evicted first.
const MAX_ENTRIES = 100;

type Entry = { data: BarsResponse; fetchedAt: number };

const cache = new Map<string, Entry>();
const inflight = new Map<string, Promise<BarsResponse>>();

// The ext flag is part of the key: an ext-inclusive series and an RTH-only series
// for the same symbol/timeframe/days are different payloads, so a timeframe or ETH
// round-trip must not serve the wrong variant from cache.
function keyOf(
  symbol: string,
  timeframe: string,
  days: number,
  extended: boolean,
): string {
  return `${symbol}|${timeframe}|${days}|${extended ? "ext" : "rth"}`;
}

function evictOldestIfFull(): void {
  if (cache.size < MAX_ENTRIES) return;
  let oldestKey: string | null = null;
  let oldest = Infinity;
  for (const [k, entry] of cache) {
    if (entry.fetchedAt < oldest) {
      oldest = entry.fetchedAt;
      oldestKey = k;
    }
  }
  if (oldestKey) cache.delete(oldestKey);
}

/**
 * Cached wrapper around fetchBars. Returns a fresh cache hit instantly (no
 * network); on a miss/expiry it fetches, de-duping concurrent identical
 * requests so a multi-panel grid asking for the same key shares one call.
 */
export async function fetchBarsCached(
  symbol: string,
  options: { timeframe: string; days: number; extended: boolean },
): Promise<BarsResponse> {
  const { timeframe, days, extended } = options;
  const key = keyOf(symbol, timeframe, days, extended);
  const ttl = TTL_MS[timeframe] ?? DEFAULT_TTL_MS;

  const entry = cache.get(key);
  if (entry && Date.now() - entry.fetchedAt < ttl) {
    return entry.data;
  }

  const existing = inflight.get(key);
  if (existing) return existing;

  const promise = fetchBars(symbol, { timeframe, days, extended })
    .then((data) => {
      evictOldestIfFull();
      cache.set(key, { data, fetchedAt: Date.now() });
      return data;
    })
    .finally(() => {
      inflight.delete(key);
    });
  inflight.set(key, promise);
  return promise;
}

/**
 * Drop a cached entry so the next fetchBarsCached for this key hits the network.
 * Used by the chart when its live-tick layer detects the cached tail has gone
 * stale (page was backgrounded past the freshness window) and needs a real
 * refetch rather than merging across the gap.
 */
export function invalidateBars(
  symbol: string,
  timeframe: string,
  days: number,
  extended: boolean,
): void {
  cache.delete(keyOf(symbol, timeframe, days, extended));
}

// --- Persist across a full page reload -----------------------------------------
//
// The cache above is a plain in-memory Map, so a hard reload (F5, restored tab)
// dropped every chart back to cold — even though the entries were still within
// their TTL. We mirror the cache into sessionStorage: rehydrate on load, flush on
// tab-hide. Persistence is transparent to callers — fetchBarsCached still applies
// the TTL, so a rehydrated-but-expired entry refetches exactly as before; the win
// is that a within-TTL reload paints instantly with no network round-trip.
//
// sessionStorage (not local): survives reload + same-tab nav, clears on tab close —
// the exact lifetime we want, with no unbounded cross-session growth. All access is
// best-effort and guarded: private-mode/quota failures degrade to a cold start.

function barsStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null; // sessionStorage getter can throw under strict privacy settings
  }
}

function hydrateCacheFromStorage(): void {
  const store = barsStorage();
  if (!store) return;
  for (const [key, entry] of parsePersisted(store.getItem(BARS_CACHE_STORAGE_KEY))) {
    if (cache.size >= MAX_ENTRIES) break;
    // Shape validated by parsePersisted (data + fetchedAt present); the payload is
    // our own prior serialization, so narrow back to Entry at this trust boundary.
    cache.set(key, entry as Entry);
  }
}

function persistCacheToStorage(): void {
  const store = barsStorage();
  if (!store) return;
  try {
    const kept = selectPersistable([...cache.entries()], BARS_CACHE_PERSIST_BUDGET_BYTES);
    store.setItem(BARS_CACHE_STORAGE_KEY, JSON.stringify(kept));
  } catch {
    // QuotaExceeded / serialization failure — drop the blob rather than half-write.
    try {
      store.removeItem(BARS_CACHE_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }
}

if (typeof window !== "undefined") {
  hydrateCacheFromStorage();
  const flush = () => persistCacheToStorage();
  // pagehide covers reload/close; visibilitychange→hidden is the reliable mobile path.
  window.addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flush();
  });
}
