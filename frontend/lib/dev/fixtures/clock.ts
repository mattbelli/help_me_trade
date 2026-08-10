/** Frozen clock + tiny deterministic generators for the /dev/gallery fixtures.
 *
 * Everything here is a constant or a pure function of a seed — no Date.now(),
 * no Math.random(). Two runs of `tools/ui-shot.mjs` a week apart must produce
 * byte-identical widget bodies, so a screenshot diff means the UI changed.
 */

/** The frozen "today" every fixture is written against (a Friday). */
export const DAY = "2026-08-07";

/** Frozen wall clock: 2026-08-07 15:45 ET, mid-afternoon on that session. */
export const NOW_ISO = "2026-08-07T19:45:00Z";

/** ET-offset ISO for that session (EDT = UTC-4) — what the API actually emits. */
export function et(time: string, day = DAY): string {
  return `${day}T${time}-04:00`;
}

/** `daysBefore(3)` -> "2026-08-04". Calendar-correct, no Date at module scope. */
export function daysBefore(n: number, from = DAY): string {
  const d = new Date(`${from}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

export function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export function money(n: number): string {
  return n.toFixed(2);
}

/**
 * Seeded LCG — the same seed always yields the same series. Used for sparklines
 * and candle walks so charts look alive but never move between runs.
 *
 * The seed is hashed and the generator warmed up first. Without that, adjacent
 * seeds (lcg(700), lcg(701), …) return nearly identical first draws, and every
 * row of a table built one-seed-per-index came out the same — the desk widget
 * showed the same three z-scores over and over and every journal row was a win.
 */
export function lcg(seed: number): () => number {
  let s = Math.imul(seed >>> 0, 2654435761) >>> 0;
  const next = () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
  next();
  next();
  next();
  return next;
}

/** Deterministic wiggle around `base`, e.g. a 40-point sparkline. Clamped to a
 *  ±25% band so a low-priced ticker's walk can't drift through zero and paint a
 *  sparkline cliff. */
export function series(base: number, points: number, ampPct: number, seed: number): number[] {
  const rand = lcg(seed);
  const floor = base * 0.75;
  const ceil = base * 1.25;
  const out: number[] = [];
  let v = base;
  for (let i = 0; i < points; i += 1) {
    v = Math.min(ceil, Math.max(floor, v + base * ampPct * (rand() - 0.48)));
    out.push(round2(v));
  }
  return out;
}
