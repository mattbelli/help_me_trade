"use client";

import { useEffect, useRef } from "react";
import { motion } from "motion/react";
import { ChevronRightIcon, MessageSquareIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useCopilotStore } from "@/lib/workspace/use-copilot-store";

import { WorkspaceThread } from "./workspace-chat";

const EXPANDED_WIDTH = 380;
const COLLAPSED_WIDTH = 44;

export function CopilotSidebar() {
  const open = useCopilotStore((s) => s.open);
  const setOpen = useCopilotStore((s) => s.setOpen);
  const hydrate = useCopilotStore((s) => s.hydrate);
  const sidebarRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (sidebarRef.current?.contains(target)) return;
      setOpen(false);
    }

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, setOpen]);

  return (
    <motion.div
      ref={sidebarRef}
      className="flex shrink-0 flex-col overflow-hidden border-l border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)]"
      animate={{ width: open ? EXPANDED_WIDTH : COLLAPSED_WIDTH }}
      initial={false}
      transition={{ type: "spring", stiffness: 420, damping: 36 }}
    >
      {open ? (
        <>
          <div className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--border)] px-3">
            <span className="font-sans text-[12px] font-medium text-foreground">
              Copilot
            </span>
            <div className="flex-1" />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
              onClick={() => setOpen(false)}
              aria-label="Collapse copilot"
            >
              <ChevronRightIcon className="size-4" />
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            <WorkspaceThread />
          </div>
        </>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={cn(
            "flex h-full w-full flex-col items-center justify-center gap-3 py-4",
            "text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground",
          )}
          aria-label="Open copilot"
        >
          <MessageSquareIcon className="size-4 shrink-0" />
          <span
            className="font-sans text-[10px] font-medium tracking-wide"
            style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
          >
            Copilot
          </span>
        </button>
      )}
    </motion.div>
  );
}
