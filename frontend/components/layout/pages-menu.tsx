"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Check, LayoutPanelLeft } from "lucide-react";

import { cn } from "@/lib/utils";

export const TOP_LEVEL_PAGES = [
  { href: "/", label: "Workspace" },
  { href: "/board", label: "Board" },
  { href: "/research", label: "Research" },
  { href: "/strategy-lab", label: "Strategy Lab" },
] as const;

function isActivePage(href: string, pathname: string | null): boolean {
  if (!pathname) return false;
  if (href === "/") {
    // Workspace owns the root URL (and the legacy /workspace redirect).
    return pathname === "/" || pathname.startsWith("/workspace");
  }
  return pathname.startsWith(href);
}

function resolveCurrentPage(pathname: string | null) {
  return (
    TOP_LEVEL_PAGES.find((page) => isActivePage(page.href, pathname)) ??
    TOP_LEVEL_PAGES[0]
  );
}

type PagesMenuProps = {
  className?: string;
  /** Hide the current page label on very narrow headers. */
  hideLabel?: boolean;
};

// Deliberately NOT the Base UI dropdown: its menu primitive crashed in the
// production build (minified error #31) when opening. Three links don't need
// a primitive — a plain popover can't break.
export function PagesMenu({ className, hideLabel = false }: PagesMenuProps) {
  const pathname = usePathname();
  const router = useRouter();
  const current = resolveCurrentPage(pathname);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Switch page"
        aria-expanded={open}
        className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-[3px] text-[var(--fg-dim)] outline-none transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)] focus-visible:ring-2 focus-visible:ring-[var(--c-accent)]"
      >
        <LayoutPanelLeft className="size-3.5 shrink-0" />
        {!hideLabel ? (
          <span className="max-w-[7rem] truncate font-mono text-[11px]">
            {current.label}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute top-full left-0 z-50 mt-1 min-w-[10.5rem] rounded-md border border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[11px] shadow-[var(--panel-shadow)]">
          <div className="px-2 py-1 text-[9px] tracking-wide text-[var(--fg-faint)] uppercase">
            Pages
          </div>
          {TOP_LEVEL_PAGES.map((page) => {
            const active = isActivePage(page.href, pathname);
            return (
              <button
                key={page.href}
                type="button"
                onClick={() => {
                  setOpen(false);
                  router.push(page.href);
                }}
                className={cn(
                  "flex w-full cursor-pointer items-center gap-1.5 rounded px-2 py-1 text-left font-mono text-[11px] text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
                  active && "bg-[var(--bg-hover)] text-foreground",
                )}
              >
                <span className="flex-1">{page.label}</span>
                {active ? (
                  <Check className="size-3.5 text-[var(--c-accent)]" />
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
