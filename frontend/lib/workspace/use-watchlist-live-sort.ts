"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import { flushSync } from "react-dom";
import { useReducedMotion } from "motion/react";

import {
  fullSort,
  ordersEqual,
  reconcileOrder,
  stickySort,
  type SortState,
  type SortValue,
} from "@/lib/workspace/watchlist-sort";

export const WATCHLIST_RANK_MS = 550;
const LIVE_RANK_INTERVAL_MS = 2_000;
const POINTER_LEAVE_GRACE_MS = 1_000;

type ViewTransitionLike = {
  ready: Promise<void>;
  finished: Promise<void>;
  skipTransition: () => void;
};

type ViewTransitionHost = HTMLElement & {
  startViewTransition?: (update: () => void) => ViewTransitionLike;
};

function vtIdent(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "_");
}

export function watchlistRowVtName(instanceId: string, symbol: string): string {
  return `wl-${vtIdent(instanceId)}-${vtIdent(symbol)}`;
}

function captureTops(
  nodes: Map<string, HTMLElement>,
  scope: HTMLElement,
): Map<string, number> {
  const scopeTop = scope.getBoundingClientRect().top;
  const tops = new Map<string, number>();
  for (const [sym, el] of nodes) {
    tops.set(sym, el.getBoundingClientRect().top - scopeTop);
  }
  return tops;
}

function playFlip(
  nodes: Map<string, HTMLElement>,
  firstTops: Map<string, number>,
  lastTops: Map<string, number>,
): Animation[] {
  const animations: Animation[] = [];
  for (const [sym, el] of nodes) {
    const first = firstTops.get(sym);
    const last = lastTops.get(sym);
    if (first == null || last == null) continue;
    const dy = first - last;
    if (Math.abs(dy) < 1.5) continue;

    animations.push(
      el.animate(
        [
          { transform: `translateY(${dy}px)` },
          { transform: "translateY(0px)" },
        ],
        {
          id: "wl-flip",
          duration: WATCHLIST_RANK_MS,
          easing: "cubic-bezier(0.4, 0, 0.2, 1)",
          fill: "none",
        },
      ),
    );
  }
  return animations;
}

export function useWatchlistLiveSort({
  sortState,
  baseOrder,
  getValue,
  isDragging,
  pageActive,
  instanceId,
  scrollerRef,
}: {
  sortState: SortState | null;
  baseOrder: string[];
  getValue: (sym: string) => SortValue;
  isDragging: boolean;
  pageActive: boolean;
  instanceId: string;
  scrollerRef: RefObject<HTMLElement | null>;
}): {
  liveOrder: string[] | null;
  scopeRef: RefObject<HTMLDivElement | null>;
  registerRow: (symbol: string, el: HTMLLIElement | null) => void;
  rowVtStyle: (symbol: string) => CSSProperties | undefined;
  animateLayout: boolean;
} {
  const reduceMotion = useReducedMotion();
  const [liveOrder, setLiveOrder] = useState<string[] | null>(null);
  const scopeRef = useRef<HTMLDivElement>(null);
  const nodesRef = useRef(new Map<string, HTMLElement>());
  const liveOrderRef = useRef<string[] | null>(null);
  const sortIdRef = useRef<string | null>(null);
  const nextRankAtRef = useRef(0);
  const resumeAtRef = useRef(0);
  const pointerOverRef = useRef(false);
  const getValueRef = useRef(getValue);
  useEffect(() => {
    getValueRef.current = getValue;
  }, [getValue]);

  const registerRow = useCallback(
    (symbol: string, el: HTMLLIElement | null) => {
      if (el) nodesRef.current.set(symbol, el);
      else nodesRef.current.delete(symbol);
    },
    [],
  );

  useEffect(() => {
    if (!sortState || isDragging) {
      sortIdRef.current = null;
      liveOrderRef.current = null;
      setLiveOrder(null);
      return;
    }

    let active = true;
    let timer: number | undefined;
    let transition: ViewTransitionLike | undefined;
    let animations: Animation[] = [];
    let animating = false;
    const sortId = `${sortState.key}:${sortState.dir}`;
    const scroller = scrollerRef.current;
    // A header click may enable sorting with the pointer already inside.
    pointerOverRef.current =
      window.matchMedia("(any-hover: hover)").matches &&
      (scroller?.matches(":hover") ?? false);

    const schedule = () => {
      window.clearTimeout(timer);
      if (!active || !pageActive || pointerOverRef.current || animating) return;
      const due = Math.max(nextRankAtRef.current, resumeAtRef.current);
      timer = window.setTimeout(rank, Math.max(0, due - Date.now()));
    };

    const finish = () => {
      if (!active) return;
      animating = false;
      schedule();
    };

    const commit = (next: string[]) => {
      const prev = liveOrderRef.current ?? baseOrder;
      const apply = () => {
        // An old browser snapshot must not restore a cleared sort or list.
        if (!active) return;
        sortIdRef.current = sortId;
        liveOrderRef.current = next;
        flushSync(() => setLiveOrder(next));
      };
      // Record an explicit sort even when its order matches the persisted list.
      if (ordersEqual(prev, next)) {
        sortIdRef.current = sortId;
        if (liveOrderRef.current === null) apply();
        return;
      }

      const scope = scopeRef.current as ViewTransitionHost | null;
      if (!pageActive || reduceMotion || !scope) {
        apply();
        return;
      }
      if (typeof scope.startViewTransition === "function") {
        animating = true;
        try {
          transition = scope.startViewTransition(apply);
          // Rapid header clicks intentionally skip the previous snapshot.
          void transition.ready.catch(() => {});
          void transition.finished.then(finish, finish);
          return;
        } catch {
          animating = false;
        }
      }
      // Transforming the real rows would break the frozen symbol column here.
      if ((scroller?.scrollLeft ?? 0) > 0) {
        apply();
        return;
      }
      const firstTops = captureTops(nodesRef.current, scope);
      apply();
      animations = playFlip(
        nodesRef.current,
        firstTops,
        captureTops(nodesRef.current, scope),
      );
      animating = animations.length > 0;
      if (animating) {
        void Promise.allSettled(
          animations.map((animation) => animation.finished),
        ).then(finish);
      }
    };

    function rank() {
      if (!active) return;
      const switched = sortIdRef.current !== sortId;
      const current = reconcileOrder(
        liveOrderRef.current ?? baseOrder,
        baseOrder,
      );
      const held = pointerOverRef.current || Date.now() < resumeAtRef.current;
      if (
        switched ||
        (!held && pageActive && Date.now() >= nextRankAtRef.current)
      ) {
        nextRankAtRef.current = Date.now() + LIVE_RANK_INTERVAL_MS;
        commit(
          switched
            ? fullSort(current, getValueRef.current, sortState!)
            : stickySort(current, getValueRef.current, sortState!),
        );
      } else if (!ordersEqual(current, liveOrderRef.current ?? baseOrder)) {
        // Adds/removals still appear while automatic ranking is held.
        commit(current);
      }
      schedule();
    }

    const enter = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      pointerOverRef.current = true;
      window.clearTimeout(timer);
    };
    const leave = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      pointerOverRef.current = false;
      resumeAtRef.current = Date.now() + POINTER_LEAVE_GRACE_MS;
      schedule();
    };
    scroller?.addEventListener("pointerenter", enter);
    scroller?.addEventListener("pointerleave", leave);
    // Run outside the effect so FLIP can flush the new row layout synchronously.
    // Header changes bypass the pointer hold and cadence, including mid-glide.
    timer = window.setTimeout(rank, 0);

    return () => {
      active = false;
      window.clearTimeout(timer);
      scroller?.removeEventListener("pointerenter", enter);
      scroller?.removeEventListener("pointerleave", leave);
      transition?.skipTransition();
      for (const animation of animations) animation.cancel();
    };
  }, [sortState, baseOrder, isDragging, pageActive, reduceMotion, scrollerRef]);

  const rowVtStyle = useCallback(
    (symbol: string): CSSProperties | undefined =>
      sortState
        ? {
            viewTransitionName: watchlistRowVtName(instanceId, symbol),
            viewTransitionClass: "wl-row",
          }
        : undefined,
    [instanceId, sortState],
  );

  return {
    liveOrder,
    scopeRef,
    registerRow,
    rowVtStyle,
    animateLayout: isDragging,
  };
}
