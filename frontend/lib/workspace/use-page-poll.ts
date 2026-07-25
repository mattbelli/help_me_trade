"use client";

import { useEffect, useRef } from "react";

import { usePageActive } from "./page-active-context";

/**
 * Interval polling that runs only while the widget's workspace page is showing.
 *
 * Every page stays MOUNTED on purpose so Alt+1/2/3 switching is instant (see
 * `page-active-context`) — that must not change. What was wasteful is a hidden
 * page's widgets refetching data nobody is looking at: 36 widgets across 6 pages
 * all polled continuously while exactly one page was visible. So the mount
 * stays and the interval doesn't — a hidden page stops fetching and refetches
 * the instant it becomes visible again. Widgets already separate first load from
 * background refresh (the `isInitial` convention), so a page switch paints the
 * data that is already on screen and swaps in fresh data with no spinner.
 *
 * `run` is called with `true` only on a widget instance's very first poll, which
 * is exactly what those `isInitial` callbacks expect. Pass a `useCallback`-stable
 * function: like the hand-rolled effects this replaces, a new identity (a changed
 * filter, a new symbol) re-runs immediately rather than waiting out the interval.
 *
 * Intervals carry ±15% jitter. Widgets sharing a cadence — six at 60s — otherwise
 * stay in lockstep forever and land on the API as one burst every cycle. The
 * first run is never delayed, so first paint is as fast as it ever was.
 */
export function usePagePoll(
  run: (isFirst: boolean) => void | Promise<void>,
  intervalMs: number,
  enabled = true,
): void {
  const active = usePageActive();
  const firstRef = useRef(true);

  useEffect(() => {
    if (!enabled) return;

    // The FIRST load ignores visibility. Hidden pages are mounted precisely so a
    // switch paints instantly, and that only works if they already hold data —
    // gating the initial fetch would trade an interval for a cold load with a
    // spinner on every first visit, which is the thing being protected here.
    // After that, only a visible page fetches: hiding costs nothing, showing
    // refreshes, and a hidden page that changed inputs refetches when shown.
    if (active || firstRef.current) {
      void run(firstRef.current);
      firstRef.current = false;
    }

    if (!active) return;

    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      timer = setTimeout(
        () => {
          void run(false);
          schedule();
        },
        intervalMs * (0.85 + Math.random() * 0.3),
      );
    };
    schedule();

    return () => clearTimeout(timer);
  }, [run, intervalMs, enabled, active]);
}
