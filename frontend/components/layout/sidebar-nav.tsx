"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bot,
  Brain,
  CandlestickChart,
  LayoutDashboard,
  LayoutGrid,
  Search,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_ITEMS: {
  href: string;
  label: string;
  description: string;
  icon: LucideIcon;
  soon?: boolean;
}[] = [
  {
    href: "/",
    label: "Overview",
    description: "Account & positions",
    icon: LayoutDashboard,
  },
  {
    href: "/trading",
    label: "Trading",
    description: "Chart & watchlist",
    icon: CandlestickChart,
  },
  {
    href: "/charts",
    label: "Charts",
    description: "Multi-chart grid",
    icon: LayoutGrid,
  },
  {
    href: "/research",
    label: "Research",
    description: "Deep dives",
    icon: Search,
    soon: true,
  },
  {
    href: "/memories",
    label: "Memories",
    description: "Agent recall",
    icon: Brain,
    soon: true,
  },
  {
    href: "/agents",
    label: "Agents",
    description: "Runs & CLI",
    icon: Bot,
    soon: true,
  },
];

type Props = {
  collapsed?: boolean;
};

export function SidebarNav({ collapsed = false }: Props) {
  const pathname = usePathname();

  return (
    <nav
      className={cn(
        "flex flex-1 flex-col gap-0.5 py-3",
        collapsed ? "px-1.5" : "px-2.5",
      )}
    >
      <p
        className={cn(
          "mb-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground transition-all duration-[280ms] ease-[cubic-bezier(0.16,1,0.3,1)]",
          collapsed && "mb-1 h-0 overflow-hidden opacity-0",
        )}
      >
        Workspace
      </p>
      {NAV_ITEMS.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href);
        const Icon = item.icon;

        return (
          <Link
            key={item.href}
            href={item.href}
            title={collapsed ? item.label : undefined}
            aria-label={item.label}
            className={cn(
              "rounded-md border border-transparent transition-[background-color,border-color,color] duration-200",
              collapsed
                ? "flex items-center justify-center px-0 py-2.5"
                : "px-2.5 py-2",
              active
                ? "border-border/80 bg-card/80 text-foreground"
                : "text-muted-foreground hover:bg-muted/40 hover:text-foreground",
            )}
          >
            {collapsed ? (
              <Icon
                className="size-[18px] shrink-0"
                strokeWidth={1.75}
                aria-hidden
              />
            ) : (
              <>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <Icon
                      className="size-4 shrink-0 opacity-80"
                      strokeWidth={1.75}
                      aria-hidden
                    />
                    <span className="text-[13px] font-medium tracking-tight">
                      {item.label}
                    </span>
                  </div>
                  {item.soon && (
                    <span className="rounded bg-muted/80 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-muted-foreground">
                      Soon
                    </span>
                  )}
                </div>
                <p className="mt-0.5 pl-[26px] text-[11px] leading-snug text-muted-foreground/75">
                  {item.description}
                </p>
              </>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
