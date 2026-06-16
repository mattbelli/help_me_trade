"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, Layers } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import { SidebarNav } from "@/components/layout/sidebar-nav";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "help-me-trade-sidebar-expanded";

const SIDEBAR_EASE = "cubic-bezier(0.4, 0, 0.2, 1)";
const SIDEBAR_DURATION_MS = 480;

export function Sidebar() {
  const [expanded, setExpanded] = useState(true);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) {
      setExpanded(stored === "true");
    } else {
      // Migrate from older "pinned" key — pinned meant stay open.
      const legacy = localStorage.getItem("help-me-trade-sidebar-pinned");
      if (legacy !== null) setExpanded(legacy === "true");
    }
    setMounted(true);
  }, []);

  function toggleExpanded() {
    setExpanded((prev) => {
      const next = !prev;
      localStorage.setItem(STORAGE_KEY, String(next));
      return next;
    });
  }

  const showExpanded = !mounted || expanded;

  return (
    <aside
      data-collapsed={mounted && !expanded ? "true" : "false"}
      className={cn(
        "relative flex shrink-0 flex-col border-r border-border bg-sidebar",
        showExpanded ? "w-56" : "w-[52px]",
      )}
      style={{
        transition: `width ${SIDEBAR_DURATION_MS}ms ${SIDEBAR_EASE}`,
      }}
    >
      <div
        className={cn(
          "flex items-center border-b border-border py-4",
          showExpanded ? "px-4" : "justify-center px-0",
        )}
      >
        <div
          className={cn(
            "min-w-0 overflow-hidden",
            showExpanded
              ? "translate-x-0 opacity-100"
              : "pointer-events-none w-0 -translate-x-1 opacity-0",
          )}
          style={{
            transition: `opacity ${SIDEBAR_DURATION_MS}ms ${SIDEBAR_EASE}, transform ${SIDEBAR_DURATION_MS}ms ${SIDEBAR_EASE}`,
          }}
        >
          <p className="truncate text-[15px] font-medium tracking-tight text-foreground">
            help me trade
          </p>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            Personal trading copilot
          </p>
        </div>
        {!showExpanded && (
          <Layers
            className="size-[18px] text-muted-foreground"
            strokeWidth={1.75}
            aria-hidden
          />
        )}
      </div>

      <SidebarNav collapsed={!showExpanded} />

      <div
        className={cn(
          "mt-auto space-y-1 border-t border-border py-3",
          showExpanded ? "px-2" : "px-1.5",
        )}
      >
        <SettingsDialog collapsed={!showExpanded} />
        <div className={cn(showExpanded ? "px-2" : "flex justify-center")}>
          {showExpanded ? (
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
        onClick={toggleExpanded}
        aria-expanded={showExpanded}
        aria-label={showExpanded ? "Collapse sidebar" : "Expand sidebar"}
        className={cn(
          "absolute top-[18px] -right-3 z-20 size-6 rounded-md border-border bg-sidebar text-muted-foreground shadow-none",
          "opacity-100 hover:bg-muted hover:text-foreground",
        )}
        style={{
          transition: `transform ${SIDEBAR_DURATION_MS}ms ${SIDEBAR_EASE}`,
          transform: showExpanded ? "rotate(0deg)" : "rotate(180deg)",
        }}
      >
        <ChevronLeft className="size-3.5" strokeWidth={2} aria-hidden />
      </Button>
    </aside>
  );
}
