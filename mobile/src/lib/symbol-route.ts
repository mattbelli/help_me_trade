/**
 * Futures symbols carry a leading slash ("/ES", "/NQ"). Dropped raw into an
 * href they split the dynamic segment — `/symbol//ES` never matches
 * `/symbol/[symbol]` — so every push builds the href here and the screen
 * decodes with decodeSymbolParam().
 */
export function symbolRoute(symbol: string): `/symbol/${string}` {
  return `/symbol/${encodeURIComponent(symbol)}`;
}

/** The param may arrive still percent-encoded depending on how the href was parsed. */
export function decodeSymbolParam(raw: string): string {
  if (!raw.includes('%')) return raw;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}
