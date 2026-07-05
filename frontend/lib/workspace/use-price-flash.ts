import { useEffect, useState } from "react";
import { useReducedMotion } from "motion/react";

export type FlashDirection = "up" | "down" | null;

type FlashState = {
  prev: number | null | undefined;
  dir: FlashDirection;
  // Bumps on every real change so the reset effect re-runs (and its timer
  // restarts) even when the direction repeats on rapid ticks.
  nonce: number;
};

/**
 * Tracks the previous value and returns "up"/"down" for 500ms after a change
 * (the timer resets on rapid ticks), then null. Always null under reduced
 * motion so a price cell can drop the flash entirely for that preference.
 */
export function usePriceFlash(value: number | null | undefined): FlashDirection {
  const reduce = useReducedMotion();
  const [state, setState] = useState<FlashState>({
    prev: value,
    dir: null,
    nonce: 0,
  });

  // Compare against the previous value while rendering (React's endorsed
  // "adjust state when a prop changes" pattern — no ref, no effect).
  if (value !== state.prev) {
    setState((s) => {
      if (value === s.prev) return s;
      if (value != null && s.prev != null) {
        return {
          prev: value,
          dir: value > s.prev ? "up" : "down",
          nonce: s.nonce + 1,
        };
      }
      return { ...s, prev: value };
    });
  }

  useEffect(() => {
    if (!state.dir) return;
    const id = setTimeout(
      () => setState((s) => (s.dir ? { ...s, dir: null } : s)),
      500,
    );
    return () => clearTimeout(id);
  }, [state]);

  return reduce ? null : state.dir;
}
