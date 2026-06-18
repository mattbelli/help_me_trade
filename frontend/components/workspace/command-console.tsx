"use client";

import { useCallback, useEffect, useRef, type PointerEvent as ReactPointerEvent } from "react";
import { motion } from "motion/react";
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useConsoleStore } from "@/lib/workspace/use-console-store";

import { WorkspaceCmdPalette } from "./workspace-cmd-palette";
import { WorkspaceThread } from "./workspace-chat";

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

export function CommandConsole() {
  const {
    open,
    height,
    mode,
    paletteOpen,
    collapsedHeight,
    toggle,
    setOpen,
    setHeight,
    toggleMode,
    openPalette,
  } = useConsoleStore();

  const consoleRef = useRef<HTMLDivElement>(null);
  const resizeRef = useRef<{ startY: number; startHeight: number } | null>(null);

  const onResizePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!open) return;
      event.preventDefault();
      resizeRef.current = { startY: event.clientY, startHeight: height };
      const onMove = (moveEvent: PointerEvent) => {
        if (!resizeRef.current) return;
        const delta = resizeRef.current.startY - moveEvent.clientY;
        setHeight(resizeRef.current.startHeight + delta);
      };
      const onUp = () => {
        resizeRef.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [height, open, setHeight],
  );

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) return;

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        toggle();
        return;
      }

      if (
        event.key === "/" &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !isTypingTarget(event.target)
      ) {
        event.preventDefault();
        openPalette();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [openPalette, toggle]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (consoleRef.current?.contains(target)) return;
      setOpen(false);
    }

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, setOpen]);

  const panelHeight = open ? height : collapsedHeight;

  return (
    <motion.div
      ref={consoleRef}
      className="absolute inset-x-0 bottom-0 z-50 flex flex-col border-t border-[var(--border)] bg-[var(--bg-raised)] shadow-[0_-10px_40px_rgba(0,0,0,0.3)]"
      animate={{ height: panelHeight }}
      initial={false}
      transition={{ type: "spring", stiffness: 420, damping: 36 }}
    >
      {open ? (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize console"
          className="absolute -top-1.5 right-0 left-0 z-10 h-2 cursor-row-resize"
          onPointerDown={onResizePointerDown}
        >
          <div className="absolute top-1 left-1/2 h-[3px] w-11 -translate-x-1/2 rounded-full bg-[var(--border-strong)]" />
        </div>
      ) : null}

      {open ? (
        <div className="min-h-0 flex-1 overflow-hidden">
          {mode === "ask" ? (
            <WorkspaceThread />
          ) : paletteOpen ? (
            <div className="h-full overflow-auto p-2">
              <WorkspaceCmdPalette />
            </div>
          ) : (
            <div className="flex h-full items-center justify-center p-4">
              <p className="font-mono text-xs text-[var(--fg-dim)]">
                Press{" "}
                <kbd className="rounded border border-[var(--border)] px-1">
                  /
                </kbd>{" "}
                for commands
              </p>
            </div>
          )}
        </div>
      ) : null}

      <div
        className={cn(
          "flex h-[46px] shrink-0 items-center gap-2.5 px-3.5",
          open && "border-t border-[var(--border)]",
        )}
      >
        <button
          type="button"
          onClick={toggleMode}
          className={cn(
            "cursor-pointer rounded-md border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-1 font-mono text-[10.5px] font-semibold tracking-wide uppercase select-none",
            mode === "ask" ? "text-[var(--c-accent)]" : "text-[var(--c-command)]",
          )}
        >
          {mode === "ask" ? "ASK" : "CMD"}
        </button>

        <span className="font-mono text-[var(--fg-faint)]">›</span>

        <button
          type="button"
          className="flex-1 truncate text-left font-mono text-[13px] text-[var(--fg-faint)]"
          onClick={() => setOpen(true)}
        >
          {mode === "ask"
            ? "Ask your copilot, or / for commands"
            : "Type / for command palette"}
        </button>

        <span className="hidden items-center gap-1.5 font-mono text-[10.5px] text-[var(--fg-faint)] sm:flex">
          <span className="size-1.5 rounded-full bg-[var(--c-accent)]" />
          idle
        </span>

        <span className="hidden font-mono text-[10.5px] text-[var(--fg-faint)] sm:inline">
          ⌘K
        </span>

        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
          onClick={toggle}
          aria-label={open ? "Collapse console" : "Expand console"}
        >
          {open ? (
            <ChevronDownIcon className="size-4" />
          ) : (
            <ChevronUpIcon className="size-4" />
          )}
        </Button>
      </div>
    </motion.div>
  );
}
