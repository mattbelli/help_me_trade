"use client";

import { useEffect } from "react";

import {
  LAB_PAGE_ID,
  useWorkspaceStore,
} from "@/lib/workspace/use-workspace-store";

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

function isPlainAlt(event: globalThis.KeyboardEvent) {
  return event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
}

/** Elements where a bare Space has meaning of its own (activation, dialogs). */
function isInteractiveTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(
    target.closest(
      "button, a, [role='button'], [role='menuitem'], [role='menu'], [role='dialog'], [data-radix-popper-content-wrapper]",
    ),
  );
}

export function usePageHotkeys() {
  const setActivePage = useWorkspaceStore((s) => s.setActivePage);

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) return;
      if (isTypingTarget(event.target)) return;

      // Bare Space = widget palette, Spotlight-style (founder call).
      // preventDefault also kills the browser's space-scrolls-the-canvas
      // default. Skipped on buttons/links/menus where Space activates.
      if (
        event.code === "Space" &&
        !event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.shiftKey
      ) {
        if (isInteractiveTarget(event.target)) return;
        event.preventDefault();
        useWorkspaceStore.getState().setPickerOpen(true);
        return;
      }

      if (!isPlainAlt(event)) return;

      const pages = useWorkspaceStore.getState().pages;
      const pageIds = Object.keys(pages);
      if (pageIds.length === 0) return;

      const activePageId = useWorkspaceStore.getState().activePageId;
      const activeIndex = pageIds.indexOf(activePageId);

      // Match on event.code (physical key), not event.key — on Mac, Option+1
      // produces "¡" and Option+[ produces "“", so event.key would never match.
      const { code } = event;

      // Alt+L → the built-in Strategy Lab view (its own slot, not a page index).
      if (code === "KeyL") {
        event.preventDefault();
        setActivePage(LAB_PAGE_ID);
        return;
      }

      if (code.startsWith("Digit")) {
        const n = Number.parseInt(code.slice(5), 10);
        if (!(n >= 1 && n <= 5)) return;
        const index = n - 1;
        if (index >= pageIds.length) return;
        event.preventDefault();
        setActivePage(pageIds[index]);
        return;
      }

      if (code === "BracketLeft") {
        if (activeIndex <= 0) return;
        event.preventDefault();
        setActivePage(pageIds[activeIndex - 1]);
        return;
      }

      if (code === "BracketRight") {
        if (activeIndex < 0 || activeIndex >= pageIds.length - 1) return;
        event.preventDefault();
        setActivePage(pageIds[activeIndex + 1]);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [setActivePage]);
}
