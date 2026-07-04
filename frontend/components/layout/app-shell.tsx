"use client";

import { usePathname } from "next/navigation";

import { ExecutionMonitor } from "@/components/trading/execution-monitor";
import { PriceAlertMonitor } from "@/components/trading/price-alert-monitor";
import { SnapshotProvider } from "@/components/providers/snapshot-provider";

import { RightRail } from "./right-rail";
import { Sidebar } from "./sidebar";

const FULL_BLEED_PREFIXES = ["/workspace", "/board", "/research"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  // "/" is the workspace now (founder decision 2026-07-04) — full-bleed too.
  const fullBleed =
    pathname === "/" ||
    FULL_BLEED_PREFIXES.some((prefix) => pathname?.startsWith(prefix));

  return (
    <SnapshotProvider>
      {fullBleed ? (
        <div className="min-h-svh bg-background">{children}</div>
      ) : (
        <div className="flex min-h-svh bg-background">
          <Sidebar />

          <div className="flex min-w-0 flex-1">
            <main className="canvas-surface min-w-0 flex-1 overflow-auto">
              <div className="mx-auto max-w-6xl px-5 py-8 lg:px-8 lg:py-10">
                {children}
              </div>
            </main>
            <RightRail />
          </div>
        </div>
      )}
      <PriceAlertMonitor />
      <ExecutionMonitor />
    </SnapshotProvider>
  );
}
