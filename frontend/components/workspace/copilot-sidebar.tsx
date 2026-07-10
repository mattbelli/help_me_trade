"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ChevronDownIcon,
  PanelLeftIcon,
  PinIcon,
  PinOffIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  COPILOT_MIN_WIDTH,
  getCopilotMaxWidth,
  useCopilotStore,
} from "@/lib/workspace/use-copilot-store";
import { motionStd } from "@/lib/workspace/motion";

import { CopilotThreadHistory } from "./copilot-thread-history";
import { WorkspaceThread } from "./workspace-chat";

const COPILOT_PANEL_BG = "color-mix(in srgb, var(--panel) 90%, transparent)";

function isInsideRadixPortal(target: Node): boolean {
  return target instanceof Element && Boolean(target.closest("[data-radix-portal]"));
}

export function CopilotSidebar() {
  const [historyOpen, setHistoryOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const open = useCopilotStore((s) => s.open);
  const pinned = useCopilotStore((s) => s.pinned);
  const width = useCopilotStore((s) => s.width);
  const setOpen = useCopilotStore((s) => s.setOpen);
  const setWidth = useCopilotStore((s) => s.setWidth);
  const togglePinned = useCopilotStore((s) => s.togglePinned);
  const hydrate = useCopilotStore((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!historyOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setHistoryOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [historyOpen]);

  useEffect(() => {
    if (!open || pinned) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (panelRef.current?.contains(target)) return;
      if (
        target instanceof Element &&
        target.closest("[data-copilot-toggle]")
      ) {
        return;
      }
      if (isInsideRadixPortal(target)) return;
      setOpen(false);
    }

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, pinned, setOpen]);

  const onResizePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = width;

      function onMove(moveEvent: PointerEvent) {
        const delta = startX - moveEvent.clientX;
        const next = Math.min(
          getCopilotMaxWidth(),
          Math.max(COPILOT_MIN_WIDTH, startWidth + delta),
        );
        setWidth(next);
      }

      function onUp() {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
      }

      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
    },
    [setWidth, width],
  );

  // No launcher pill when closed (founder call) — Ctrl/Cmd+J opens the copilot.
  if (!open) return null;

  return (
    <motion.div
      ref={panelRef}
      className={cn(
        "absolute z-40 flex flex-col overflow-hidden border border-[var(--border)] shadow-[var(--panel-shadow)] backdrop-blur-md",
        pinned
          ? "top-0 right-0 bottom-0 rounded-none border-r-0 border-t-0 border-b-0"
          : "bottom-3 right-3 h-[32rem] max-h-[calc(100svh-6rem)] rounded-lg",
      )}
      style={{ width, backgroundColor: COPILOT_PANEL_BG }}
      initial={false}
      animate={{ width }}
      transition={motionStd}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize copilot"
        className="absolute top-0 bottom-0 left-0 z-30 w-1.5 cursor-col-resize hover:bg-[var(--border-strong)]"
        onPointerDown={onResizePointerDown}
      />

      <div className="relative z-40 flex h-10 shrink-0 items-center gap-2 border-b border-[var(--border)] px-3">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className={cn(
            "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
            historyOpen && "bg-[var(--bg-hover)] text-foreground",
          )}
          onClick={() => setHistoryOpen((prev) => !prev)}
          aria-label="Chat history"
          aria-expanded={historyOpen}
        >
          <PanelLeftIcon className="size-4" />
        </Button>
        <span className="font-mono text-[length:var(--fs-title)] font-medium text-foreground">
          Copilot
        </span>
        <div className="flex-1" />
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className={cn(
            "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
            pinned && "text-foreground",
          )}
          onClick={togglePinned}
          aria-label={pinned ? "Unpin copilot" : "Pin copilot"}
          title={pinned ? "Float over workspace" : "Pin to sidebar"}
        >
          {pinned ? (
            <PinOffIcon className="size-4" />
          ) : (
            <PinIcon className="size-4" />
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
          onClick={() => setOpen(false)}
          aria-label="Collapse copilot"
        >
          <ChevronDownIcon className="size-4" />
        </Button>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden bg-[var(--bg-inset)]">
        <WorkspaceThread mono />

        <AnimatePresence>
          {historyOpen ? (
            <>
              <motion.div
                key="copilot-history-backdrop"
                className="absolute inset-0 z-10"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onPointerDown={() => setHistoryOpen(false)}
              />
              <motion.div
                key="copilot-history-panel"
                className={cn(
                  "absolute left-0 top-0 bottom-0 z-20 w-[15rem] max-w-[85%] overflow-y-auto",
                  "border-r border-[var(--border)] bg-[var(--panel)]/5 backdrop-blur-lg",
                )}
                initial={{ x: -12, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ x: -12, opacity: 0 }}
                transition={motionStd}
              >
                <CopilotThreadHistory
                  onAfterSelect={() => setHistoryOpen(false)}
                />
              </motion.div>
            </>
          ) : null}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
