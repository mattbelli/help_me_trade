"use client";

import { useEffect, useState } from "react";
import { Layers } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import { SidebarNav } from "@/components/layout/sidebar-nav";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "help-me-trade-sidebar-pinned";

export function Sidebar() {
  const [pinned, setPinned] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [mounted, setMounted] = useState(false);

  const open = pinned || hovering;

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "true") setPinned(true);
    setMounted(true);
  }, []);

  function togglePin() {
    setPinned((prev) => {
      const next = !prev;
      localStorage.setItem(STORAGE_KEY, String(next));
      return next;
    });
  }

  return (
    <aside
      data-collapsed={mounted && !open ? "true" : "false"}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className={cn(
        "group/sidebar relative flex shrink-0 flex-col border-r border-border bg-sidebar",
        "transition-[width] duration-[320ms] ease-[cubic-bezier(0.16,1,0.3,1)]",
        open ? "w-56" : "w-[52px]",
      )}
    >
      <div
        className={cn(
          "flex items-center border-b border-border py-4",
          open ? "px-4" : "justify-center px-0",
        )}
      >
        <div
          className={cn(
            "min-w-0 overflow-hidden transition-[opacity,transform] duration-[280ms] ease-[cubic-bezier(0.16,1,0.3,1)]",
            open
              ? "translate-x-0 opacity-100"
              : "pointer-events-none w-0 -translate-x-2 opacity-0",
          )}
        >
          <p className="truncate text-[15px] font-medium tracking-tight text-foreground">
            help me trade
          </p>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            Personal trading copilot
          </p>
        </div>
        {!open && (
          <Layers
            className="size-[18px] text-muted-foreground"
            strokeWidth={1.75}
            aria-hidden
          />
        )}
      </div>

      <SidebarNav collapsed={!open} />

      <div
        className={cn(
          "mt-auto space-y-1 border-t border-border py-3",
          open ? "px-2" : "px-1.5",
        )}
      >
        <SettingsDialog collapsed={!open} />
        <div className={cn(open ? "px-2" : "flex justify-center")}>
          {open ? (
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Paper mode
            </p>
          ) : (
            <span
              className="size-1.5 rounded-full bg-muted-foreground/40"
              title="Paper mode"
            />
          )}
        </div>
      </div>

      <Button
        type="button"
        variant="outline"
        size="icon-sm"
        onClick={togglePin}
        aria-expanded={open}
        aria-pressed={pinned}
        aria-label={
          pinned ? "Unpin sidebar (auto-collapse)" : "Pin sidebar open"
        }
        className={cn(
          "absolute top-[18px] -right-3 z-20 size-6 rounded-md border-border bg-sidebar text-muted-foreground shadow-none",
          "transition-[transform,opacity] duration-[320ms] ease-[cubic-bezier(0.16,1,0.3,1)] hover:bg-muted hover:text-foreground",
          pinned ? "opacity-100" : "opacity-0 group-hover/sidebar:opacity-100",
          open ? "rotate-0" : "rotate-180",
        )}
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          aria-hidden
        >
          <path
            d="M7.5 2.5 4 6l3.5 3.5"
            stroke="currentColor"
            strokeWidth="1.25"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </Button>
    </aside>
  );
}
