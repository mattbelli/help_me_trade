"use client";

import { useCallback, useEffect } from "react";
import { motion } from "motion/react";
import {
  ChevronDownIcon,
  MessageSquareIcon,
  PinIcon,
  PinOffIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  COPILOT_MAX_WIDTH,
  COPILOT_MIN_WIDTH,
  useCopilotStore,
} from "@/lib/workspace/use-copilot-store";

import { CopilotThreadHistory } from "./copilot-thread-history";
import { WorkspaceThread } from "./workspace-chat";

export function CopilotSidebar() {
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

  const onResizePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = width;

      function onMove(moveEvent: PointerEvent) {
        const delta = startX - moveEvent.clientX;
        const next = Math.min(
          COPILOT_MAX_WIDTH,
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

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "absolute bottom-3 right-3 z-40 flex items-center gap-2 rounded-lg border border-[var(--border)]",
          "bg-[var(--bg-raised)]/85 px-3 py-2 shadow-lg backdrop-blur-md",
          "font-sans text-[12px] font-medium text-[var(--fg-dim)] transition-colors",
          "hover:bg-[var(--bg-hover)] hover:text-foreground",
        )}
        aria-label="Open copilot"
      >
        <MessageSquareIcon className="size-4 shrink-0" />
        Copilot
      </button>
    );
  }

  return (
    <motion.div
      className={cn(
        "absolute z-40 flex flex-col overflow-hidden border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)]",
        pinned
          ? "top-0 right-0 bottom-0 rounded-none border-r-0 border-t-0 border-b-0"
          : "bottom-3 right-3 h-[32rem] max-h-[calc(100svh-6rem)] rounded-lg",
      )}
      style={{ width }}
      initial={false}
      animate={{ width }}
      transition={{ type: "spring", stiffness: 420, damping: 36 }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize copilot"
        className="absolute top-0 bottom-0 left-0 z-10 w-1.5 cursor-col-resize hover:bg-[var(--border-strong)]"
        onPointerDown={onResizePointerDown}
      />

      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--border)] px-3 pl-4">
        <span className="font-sans text-[12px] font-medium text-foreground">
          Copilot
        </span>
        <CopilotThreadHistory />
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

      <div className="min-h-0 flex-1 overflow-hidden bg-[var(--bg-inset)]">
        <WorkspaceThread />
      </div>
    </motion.div>
  );
}
