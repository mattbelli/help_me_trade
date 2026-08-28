// Live-sort for the watchlist: a Schmitt trigger on the displayed value, not
// a re-rank on every quote tick.
//
// Sorting Δ% (or Last, volume, …) against a streaming tape will otherwise
// ping-pong any two names whose prints straddle each other — Motion then
// FLIP-animates every swap, which is the "rows flip-flopping and hairlines
// vanishing" glitch. Two names that still print as +1.23% must not trade
// places; a challenger has to beat the occupant by a full display unit
// before it takes the seat. Header clicks (key/dir change) bypass this and
// apply a full sort so the first arrangement matches the column.

export type SortDir = "asc" | "desc";
export type SortState = { key: string; dir: SortDir };

export type SortValue = number | string | null;

export function ordersEqual(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

// Keep still-present symbols in their current relative order; newly added
// symbols prepend (the add-row pins fresh names at the top) so the next
// sticky/full pass can seat them. Dropped symbols disappear.
export function reconcileOrder(
  current: readonly string[],
  base: readonly string[],
): string[] {
  const baseSet = new Set(base);
  const kept = current.filter((s) => baseSet.has(s));
  const keptSet = new Set(kept);
  const added = base.filter((s) => !keptSet.has(s));
  return added.length === 0 ? kept : [...added, ...kept];
}

function quantum(key: string, a: number, b: number): number {
  const mag = Math.max(Math.abs(a), Math.abs(b));
  switch (key) {
    case "volume":
    case "pos_qty":
      return Math.max(1, 0.0025 * mag);
    case "market_cap":
      return Math.max(1e6, 0.0025 * mag);
    case "rvol":
    case "abnorm":
    case "atr_move":
    case "pe":
      return 0.1;
    case "change_pct":
    case "gap_pct":
    case "div_yield":
    case "vs_spy":
    case "spread":
    case "pos_pl_pct":
    case "dist_52w_high":
    case "dist_52w_low":
    case "sma50_dist":
    case "sma200_dist":
      return 0.01;
    default:
      // Last, bid/ask, P&L dollars — one cent, matching the cell.
      return 0.01;
  }
}

function compareStrings(a: string, b: string, dir: SortDir): number {
  return (dir === "asc" ? 1 : -1) * a.localeCompare(b);
}

// True when the lower neighbor belongs above the upper one. Nulls always
// sink; equal quantized values never swap (that's the deadband).
function shouldSwap(
  upper: SortValue,
  lower: SortValue,
  dir: SortDir,
  key: string,
  hysteresis: boolean,
): boolean {
  const upperMissing = upper == null;
  const lowerMissing = lower == null;
  if (upperMissing && lowerMissing) return false;
  if (upperMissing) return true;
  if (lowerMissing) return false;

  if (key === "symbol" || typeof upper === "string" || typeof lower === "string") {
    return compareStrings(String(upper), String(lower), dir) > 0;
  }

  const u = upper as number;
  const l = lower as number;
  if (!Number.isFinite(u) || !Number.isFinite(l)) return false;

  if (!hysteresis) {
    if (u === l) return false;
    return dir === "desc" ? l > u : l < u;
  }

  const step = quantum(key, u, l);
  if (!(step > 0)) return false;
  const uq = Math.round(u / step);
  const lq = Math.round(l / step);
  // Challenger must beat the occupant by a full quantum — equal prints hold.
  if (dir === "desc") return lq >= uq + 1;
  return lq <= uq - 1;
}

function sortCopy(
  symbols: readonly string[],
  getValue: (sym: string) => SortValue,
  sort: SortState,
  hysteresis: boolean,
): string[] {
  const next = [...symbols];
  // Adjacent-swap bubble: with hysteresis this is a Schmitt trigger (a name
  // only climbs past a neighbor it actually outranks on the display); without,
  // it's a stable insertion of the true order. Bounded by n passes.
  for (let pass = 0; pass < next.length; pass++) {
    let moved = false;
    for (let i = 0; i < next.length - 1; i++) {
      if (
        shouldSwap(
          getValue(next[i]),
          getValue(next[i + 1]),
          sort.dir,
          sort.key,
          hysteresis,
        )
      ) {
        const tmp = next[i];
        next[i] = next[i + 1];
        next[i + 1] = tmp;
        moved = true;
      }
    }
    if (!moved) break;
  }
  return next;
}

export function fullSort(
  symbols: readonly string[],
  getValue: (sym: string) => SortValue,
  sort: SortState,
): string[] {
  return sortCopy(symbols, getValue, sort, false);
}

export function stickySort(
  current: readonly string[],
  getValue: (sym: string) => SortValue,
  sort: SortState,
): string[] {
  return sortCopy(current, getValue, sort, true);
}
