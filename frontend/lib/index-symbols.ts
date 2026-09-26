// Cash indices only exist on Schwab, under their "$" names — Alpaca has no SPX,
// so a chart asked for plain "SPX" got zero bars and a 0–1 price axis.
const INDEX_ALIASES: Record<string, string> = {
  SPX: "$SPX",
  NDX: "$NDX",
  RUT: "$RUT",
  VIX: "$VIX",
  DJI: "$DJI",
};

export function canonicalSymbol(symbol: string): string {
  const s = symbol.trim().toUpperCase();
  return INDEX_ALIASES[s] ?? s;
}
