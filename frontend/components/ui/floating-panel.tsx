"use client";

import { motion, useReducedMotion } from "motion/react";
import { XIcon } from "lucide-react";
import { createPortal } from "react-dom";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { motionStd, useMotionSafe } from "@/lib/workspace/motion";
import { TERMINAL_COLLAPSED_HEIGHT } from "@/lib/workspace/use-terminal-store";
import { cn } from "@/lib/utils";

// Magnet-snap tuning (mirrors the terminal drawer, terminal.tsx:440).
const SNAP = 24; // px threshold to lock onto an edge / center
const MARGIN = 8; // keep this much of the panel on-screen while free-dragging
const TOOLBAR_H = 52; // workspace-toolbar.tsx header height — the top snap target
// The bottom footer strip (collapsed terminal) is the vertical floor: the panel
// must never overlap it, so every bottom boundary stops at the strip's top edge
// (window height minus the strip), exactly like the terminal drawer's bottom dock.
const STRIP = TERMINAL_COLLAPSED_HEIGHT;

// Shared stacking counter across every floating panel. Base 65 keeps panels
// above the terminal drawer (z-50); each open / interaction claims the next
// value so the most-recently-touched panel wins. Reset when it drifts high so
// the number never runs away (all panels still land ≥65).
const Z_BASE = 65;
let zTop = Z_BASE - 1;
function nextZ(): number {
  zTop = zTop >= 200 ? Z_BASE : zTop + 1;
  return zTop;
}

type Pos = { left: number; top: number };

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Draggable, session-positioned floating window state with terminal-style
 * magnet snap. Drag mechanics (pointer-capture on a header handle) come from
 * the theme editor; the 24px snap math is copied from the terminal drawer and
 * extended to the vertical axis.
 *
 * Free-drag clamps the panel on-screen; on release it settles (≤120ms ease-out,
 * or instant under reduced motion) onto the nearest of six targets when within
 * SNAP px: left / right / h-center and top(=toolbar) / bottom / v-center.
 */
export function useFloatingSnap(opts: {
  open: boolean;
  /** Re-center when this changes while open; omit to seed once per open. */
  seedKey?: string | number | null;
  /** Estimated box size used only to seed a centered position. */
  width: number;
  height: number;
}) {
  const { open, seedKey, width, height } = opts;
  const reduce = useReducedMotion();
  const panelRef = useRef<HTMLDivElement>(null);
  const dragOffset = useRef<{ dx: number; dy: number } | null>(null);
  const resizeStart = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const [pos, setPos] = useState<Pos | null>(null);
  // User-resized box; null = size from the panel's own classes. Once set, it
  // overrides the class max-width/height caps (inline style wins).
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [settling, setSettling] = useState(false);
  // Stacking order — a fresh value on open and on every interaction, so the
  // clicked panel rises above its siblings.
  const [zIndex, setZIndex] = useState<number>(Z_BASE);
  const bringToFront = useCallback(() => setZIndex(nextZ()), []);

  // Seed a centered position on open (render-time "store previous prop in
  // state" pattern — no effect). Leaves pos untouched on close.
  const seedSig = open ? (seedKey != null ? String(seedKey) : "open") : null;
  const [seeded, setSeeded] = useState<string | null>(null);
  if (seeded !== seedSig) {
    setSeeded(seedSig);
    setSize(null);
    if (seedSig !== null) setZIndex(nextZ());
    if (seedSig !== null && typeof window !== "undefined") {
      const W = window.innerWidth;
      const H = window.innerHeight;
      setPos({
        left: clamp((W - width) / 2, MARGIN, Math.max(MARGIN, W - width - MARGIN)),
        top: clamp(
          Math.max(TOOLBAR_H, (H - height) / 2),
          TOOLBAR_H,
          Math.max(TOOLBAR_H, H - STRIP - height),
        ),
      });
    }
  }

  // The seed centers on an ESTIMATED box; the real rendered box can be much
  // taller (content + max-h classes). Re-clamp once after first paint (per
  // seed) so the panel never spawns hanging off-screen.
  useEffect(() => {
    if (seeded === null) return;
    const raf = requestAnimationFrame(() => {
      const el = panelRef.current;
      if (!el) return;
      const W = window.innerWidth;
      const H = window.innerHeight;
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      setPos((p) =>
        p
          ? {
              left: clamp(p.left, MARGIN, Math.max(MARGIN, W - w - MARGIN)),
              top: clamp(p.top, TOOLBAR_H, Math.max(TOOLBAR_H, H - STRIP - h)),
            }
          : p,
      );
    });
    return () => cancelAnimationFrame(raf);
  }, [seeded]);

  // The one-shot reclamp above fires once after first paint — but async content
  // (e.g. the markdown fetch) can grow the box AFTERWARD, pushing its bottom off
  // past the strip. Observe the panel and re-clamp top/left whenever the measured
  // box exceeds the boundaries. Clamp-only (an in-bounds panel is never moved),
  // and it stands down while a drag or resize is in progress.
  useEffect(() => {
    if (seeded === null) return;
    const el = panelRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (dragOffset.current || resizeStart.current) return;
      const W = window.innerWidth;
      const H = window.innerHeight;
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      setPos((p) => {
        if (!p) return p;
        const left = clamp(p.left, MARGIN, Math.max(MARGIN, W - w - MARGIN));
        const top = clamp(p.top, TOOLBAR_H, Math.max(TOOLBAR_H, H - STRIP - h));
        if (left === p.left && top === p.top) return p;
        return { left, top };
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [seeded]);

  function onPointerDown(e: React.PointerEvent) {
    if (!pos) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragOffset.current = { dx: e.clientX - pos.left, dy: e.clientY - pos.top };
    setSettling(false);
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!dragOffset.current) return;
    const el = panelRef.current;
    const w = el?.offsetWidth ?? width;
    const h = el?.offsetHeight ?? height;
    const W = window.innerWidth;
    const H = window.innerHeight;
    // Free-drag: clamp on-screen, no snapping yet (snap settles on release).
    setPos({
      left: clamp(e.clientX - dragOffset.current.dx, MARGIN, Math.max(MARGIN, W - w - MARGIN)),
      top: clamp(e.clientY - dragOffset.current.dy, TOOLBAR_H, Math.max(TOOLBAR_H, H - STRIP - h)),
    });
  }

  function onPointerUp(e: React.PointerEvent) {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    if (!dragOffset.current || !pos) return;
    dragOffset.current = null;

    const el = panelRef.current;
    const w = el?.offsetWidth ?? width;
    const h = el?.offsetHeight ?? height;
    const W = window.innerWidth;
    const H = window.innerHeight;
    let { left, top } = pos;
    // Magnets only make sense when the panel has real room to move on that
    // axis — a near-full-height panel is otherwise ALWAYS inside the bottom
    // threshold and feels glued ("latches") to the edge.
    const hRange = Math.max(0, W - w - 2 * MARGIN);
    const vRange = Math.max(0, H - STRIP - h - TOOLBAR_H);
    // Horizontal magnet: left edge / right edge / center.
    if (hRange > SNAP * 3) {
      if (Math.abs(left) < SNAP) left = 0;
      else if (Math.abs(left + w - W) < SNAP) left = W - w;
      else if (Math.abs(left + w / 2 - W / 2) < SNAP) left = (W - w) / 2;
    }
    // Vertical magnet: top (below toolbar) / bottom (flush to strip top) / center.
    if (vRange > SNAP * 3) {
      if (Math.abs(top - TOOLBAR_H) < SNAP) top = TOOLBAR_H;
      else if (Math.abs(top + h - (H - STRIP)) < SNAP) top = H - STRIP - h;
      else if (Math.abs(top + h / 2 - H / 2) < SNAP) top = (H - h) / 2;
    }

    if (!reduce && (left !== pos.left || top !== pos.top)) {
      setSettling(true);
      window.setTimeout(() => setSettling(false), 140);
    }
    setPos({ left, top });
  }

  // Bottom-right corner resize: grow/shrink the box, clamped to the viewport.
  function onResizeDown(e: React.PointerEvent) {
    if (!pos) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    const el = panelRef.current;
    resizeStart.current = {
      x: e.clientX,
      y: e.clientY,
      w: el?.offsetWidth ?? width,
      h: el?.offsetHeight ?? height,
    };
    setSettling(false);
  }

  function onResizeMove(e: React.PointerEvent) {
    if (!resizeStart.current || !pos) return;
    const { x, y, w, h } = resizeStart.current;
    setSize({
      w: clamp(w + (e.clientX - x), 320, window.innerWidth - pos.left - MARGIN),
      h: clamp(h + (e.clientY - y), 240, window.innerHeight - STRIP - pos.top),
    });
  }

  function onResizeUp(e: React.PointerEvent) {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    resizeStart.current = null;
  }

  return {
    pos,
    size,
    settling,
    zIndex,
    bringToFront,
    panelRef,
    dragHandlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
    },
    resizeHandlers: {
      onPointerDown: onResizeDown,
      onPointerMove: onResizeMove,
      onPointerUp: onResizeUp,
      onPointerCancel: onResizeUp,
    },
  };
}

/**
 * Opt-in floating window: a portaled, `fixed`, draggable panel with terminal
 * magnet-snap. Deliberately NOT a Dialog — no backdrop (the cockpit stays
 * interactive behind it), no focus trap; only Esc + the ✕ close it. Sits at
 * z ≥ 65, above the terminal drawer (z-50); interacting raises it over sibling
 * panels via a shared stacking counter. The `header` node is the drag
 * handle: wrap any interactive children inside it with
 * `onPointerDown={(e) => e.stopPropagation()}` so they don't start a drag.
 */
export function FloatingPanel({
  open,
  onClose,
  seedKey,
  initialWidth,
  initialHeight,
  ariaLabel,
  className,
  contentClassName,
  header,
  children,
}: {
  open: boolean;
  onClose: () => void;
  seedKey?: string | number | null;
  initialWidth: number;
  initialHeight: number;
  ariaLabel: string;
  /** Panel sizing box (width + max-height), merged onto the base shell. */
  className?: string;
  /** Scroll container for the body. */
  contentClassName?: string;
  header: ReactNode;
  children: ReactNode;
}) {
  const { pos, size, settling, zIndex, bringToFront, panelRef, dragHandlers, resizeHandlers } =
    useFloatingSnap({
      open,
      seedKey,
      width: initialWidth,
      height: initialHeight,
    });
  const transition = useMotionSafe(motionStd);

  if (!open || !pos) return null;

  return createPortal(
    <motion.div
      ref={panelRef}
      role="dialog"
      aria-label={ariaLabel}
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={transition}
      // Capture phase so a click anywhere in the panel raises it before drag /
      // resize / close handlers run — this only bumps z, never stops the event.
      onPointerDownCapture={bringToFront}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
      style={{
        left: pos.left,
        top: pos.top,
        zIndex,
        transition: settling ? "left 120ms ease-out, top 120ms ease-out" : "none",
        // A user resize overrides the class-based caps (inline style wins).
        ...(size
          ? { width: size.w, height: size.h, maxWidth: "none", maxHeight: "none" }
          : {}),
      }}
      className={cn(
        "fixed flex max-h-[calc(100vh-3.5rem-2.5rem)] flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel)] font-mono text-foreground shadow-[var(--panel-shadow)]",
        className,
      )}
    >
      <div
        {...dragHandlers}
        className="flex shrink-0 cursor-grab touch-none items-start gap-2 border-b border-[var(--line-divider)] bg-[var(--bg-inset)]/50 px-4 py-3 active:cursor-grabbing"
      >
        <div className="min-w-0 flex-1">{header}</div>
        <button
          type="button"
          aria-label="Close"
          onClick={onClose}
          onPointerDown={(e) => e.stopPropagation()}
          className="-mr-1 shrink-0 rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          <XIcon className="size-3.5" />
        </button>
      </div>
      <div className={cn("min-h-0 flex-1", contentClassName)}>{children}</div>
      {/* Bottom-right resize grip. */}
      <div
        {...resizeHandlers}
        aria-hidden
        className="absolute bottom-0 right-0 z-10 size-4 cursor-se-resize touch-none"
      >
        <div className="absolute bottom-1 right-1 size-2 border-b-2 border-r-2 border-[var(--fg-faint)] hover:border-[var(--fg-dim)]" />
      </div>
    </motion.div>,
    document.body,
  );
}
