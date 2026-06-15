"use client";

import { PriceAlertMonitor } from "@/components/trading/price-alert-monitor";
import { SnapshotProvider } from "@/components/providers/snapshot-provider";

import { RightRail } from "./right-rail";
import { Sidebar } from "./sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <SnapshotProvider>
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
      <PriceAlertMonitor />
    </SnapshotProvider>
  );
}
