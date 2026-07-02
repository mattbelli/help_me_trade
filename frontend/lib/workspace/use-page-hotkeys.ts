"use client";

import { useEffect } from "react";

import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";

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

export function usePageHotkeys() {
  const setActivePage = useWorkspaceStore((s) => s.setActivePage);

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) return;
      if (!isPlainAlt(event)) return;
      if (isTypingTarget(event.target)) return;

      const pages = useWorkspaceStore.getState().pages;
      const pageIds = Object.keys(pages);
      if (pageIds.length === 0) return;

      const activePageId = useWorkspaceStore.getState().activePageId;
      const activeIndex = pageIds.indexOf(activePageId);

      // Match on event.code (physical key), not event.key — on Mac, Option+1
      // produces "¡" and Option+[ produces "“", so event.key would never match.
      const { code } = event;

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
