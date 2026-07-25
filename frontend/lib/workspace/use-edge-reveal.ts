"use client";

import { useEffect, useState } from "react";

const SHOW_PX = 6; // pointer this close to the edge summons the bar
const HIDE_PX = 120; // ...and must travel this far away to dismiss it

/**
 * Auto-hiding chrome (Appearance → "Auto-hide toolbars"): reveal a docked bar
 * when the pointer nears its viewport edge, so the widget grid gets the whole
 * canvas otherwise. Returns "is this bar visible right now" — always true when
 * `enabled` is false, i.e. the bar stays put.
 *
 * `pinned` holds the bar out regardless (e.g. the terminal drawer is open).
 */
export function useEdgeReveal(
  edge: "top" | "bottom",
  enabled: boolean,
  pinned = false,
): boolean {
  const [nearEdge, setNearEdge] = useState(false);
  const [overlayOpen, setOverlayOpen] = useState(false);

  useEffect(() => {
    if (!enabled) return; // stale state is moot — a disabled bar returns true
    function onMove(event: PointerEvent) {
      const dist =
        edge === "top" ? event.clientY : window.innerHeight - event.clientY;
      setNearEdge((was) => (was ? dist < HIDE_PX : dist <= SHOW_PX));
    }
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
  }, [edge, enabled]);

  // Menus/dialogs opened from a bar portal to <body>, outside the bar and often
  // past HIDE_PX — without this the chrome slides away under the open menu.
  useEffect(() => {
    if (!enabled) return;
    const selector = '[data-radix-popper-content-wrapper], [role="dialog"]';
    const check = () => setOverlayOpen(Boolean(document.querySelector(selector)));
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.body, { childList: true });
    return () => observer.disconnect();
  }, [enabled]);

  return !enabled || pinned || nearEdge || overlayOpen;
}
