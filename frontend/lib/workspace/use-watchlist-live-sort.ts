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

export const WATCHLIST_RANK_MS = 420;

type ViewTransitionLike = {
  finished: Promise<void>;
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

function dirsFrom(
  prev: readonly string[],
  next: readonly string[],
): Map<string, "up" | "down"> {
  const prevIdx = new Map(prev.map((s, i) => [s, i]));
  const dirs = new Map<string, "up" | "down">();
  for (let i = 0; i < next.length; i++) {
    const j = prevIdx.get(next[i]);
    if (j == null || j === i) continue;
    dirs.set(next[i], i < j ? "up" : "down");
  }
  return dirs;
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
): void {
  for (const [sym, el] of nodes) {
    const first = firstTops.get(sym);
    const last = lastTops.get(sym);
    if (first == null || last == null) continue;
    const currentY = new DOMMatrix(getComputedStyle(el).transform).f;
    const dy = first + currentY - last;
    if (Math.abs(dy) < 1.5) continue;

    for (const anim of el.getAnimations()) {
      if (anim.id === "wl-flip") anim.cancel();
    }

    el.classList.remove("wl-rank-up", "wl-rank-down");
    el.classList.add(dy > 0 ? "wl-rank-up" : "wl-rank-down");
    const anim = el.animate(
      [
        { transform: `translateY(${dy}px)` },
        { transform: "translateY(0px)" },
      ],
      {
        id: "wl-flip",
        duration: WATCHLIST_RANK_MS,
        easing: "cubic-bezier(0.22, 1, 0.36, 1)",
        fill: "none",
      },
    );
    void anim.finished.then(
      () => el.classList.remove("wl-rank-up", "wl-rank-down"),
      () => el.classList.remove("wl-rank-up", "wl-rank-down"),
    );
  }
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
  const [rankDirs, setRankDirs] = useState<Map<string, "up" | "down">>(
    () => new Map(),
  );
  const scopeRef = useRef<HTMLDivElement>(null);
  const nodesRef = useRef(new Map<string, HTMLElement>());
  const liveOrderRef = useRef<string[] | null>(null);
  const sortIdRef = useRef<string | null>(null);
  const animatingRef = useRef(false);
  const pendingRef = useRef<string[] | null>(null);
  const flipTimerRef = useRef<number | null>(null);
  const getValueRef = useRef(getValue);
  const baseOrderRef = useRef(baseOrder);
  getValueRef.current = getValue;
  baseOrderRef.current = baseOrder;
  liveOrderRef.current = liveOrder;

  const registerRow = useCallback((symbol: string, el: HTMLLIElement | null) => {
    if (el) nodesRef.current.set(symbol, el);
    else nodesRef.current.delete(symbol);
  }, []);

  const commit = useCallback(
    (next: string[]) => {
      // When sort just engaged, liveOrder is still null but the DOM is showing
      // baseOrder — that's the FLIP "first" and the VT old snapshot.
      const prev = liveOrderRef.current ?? baseOrderRef.current;
      if (ordersEqual(prev, next)) return;

      if (animatingRef.current) {
        pendingRef.current = next;
        return;
      }

      const dirs = dirsFrom(prev, next);
      const canAnimate =
        pageActive && !reduceMotion && !isDragging && dirs.size > 0;

      const apply = () => {
        liveOrderRef.current = next;
        flushSync(() => {
          setRankDirs(dirs);
          setLiveOrder(next);
        });
      };

      const finish = () => {
        animatingRef.current = false;
        setRankDirs(new Map());
        const queued = pendingRef.current;
        pendingRef.current = null;
        // Sort was cleared while we were in flight — drop the queued seat.
        if (queued && sortIdRef.current) commit(queued);
      };

      if (!canAnimate) {
        apply();
        return;
      }

      const scope = scopeRef.current as ViewTransitionHost | null;
      if (scope && typeof scope.startViewTransition === "function") {
        animatingRef.current = true;
        try {
          const vt = scope.startViewTransition(apply);
          void vt.finished.then(finish, finish);
          return;
        } catch {
          animatingRef.current = false;
        }
      }

      const scrolledX = (scrollerRef.current?.scrollLeft ?? 0) > 0;
      if (scrolledX || !scope) {
        apply();
        return;
      }

      const firstTops = captureTops(nodesRef.current, scope);
      animatingRef.current = true;
      apply();
      playFlip(nodesRef.current, firstTops, captureTops(nodesRef.current, scope));
      if (flipTimerRef.current != null) window.clearTimeout(flipTimerRef.current);
      flipTimerRef.current = window.setTimeout(finish, WATCHLIST_RANK_MS);
    },
    [isDragging, pageActive, reduceMotion, scrollerRef],
  );

  useEffect(() => {
    return () => {
      if (flipTimerRef.current != null) window.clearTimeout(flipTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!sortState || isDragging) {
      sortIdRef.current = null;
      pendingRef.current = null;
      animatingRef.current = false;
      if (flipTimerRef.current != null) {
        window.clearTimeout(flipTimerRef.current);
        flipTimerRef.current = null;
      }
      if (liveOrderRef.current !== null) {
        liveOrderRef.current = null;
        setLiveOrder(null);
        setRankDirs(new Map());
      }
      return;
    }

    const sortId = `${sortState.key}:${sortState.dir}`;
    const switched = sortIdRef.current !== sortId;
    sortIdRef.current = sortId;

    const current = reconcileOrder(
      liveOrderRef.current ?? baseOrder,
      baseOrder,
    );
    const next = switched
      ? fullSort(current, getValueRef.current, sortState)
      : stickySort(current, getValueRef.current, sortState);
    commit(next);
  }, [sortState, baseOrder, getValue, isDragging, commit]);

  const rowVtStyle = useCallback(
    (symbol: string): CSSProperties | undefined => {
      if (!sortState) return undefined;
      const dir = rankDirs.get(symbol);
      return {
        viewTransitionName: watchlistRowVtName(instanceId, symbol),
        viewTransitionClass: dir ? `wl-row wl-row-${dir}` : "wl-row",
      };
    },
    [instanceId, rankDirs, sortState],
  );

  return {
    liveOrder,
    scopeRef,
    registerRow,
    rowVtStyle,
    animateLayout: isDragging,
  };
}
