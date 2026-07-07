"use client";

import { usePathname } from "next/navigation";

import { ExecutionMonitor } from "@/components/trading/execution-monitor";
import { PriceAlertMonitor } from "@/components/trading/price-alert-monitor";
import { SnapshotProvider } from "@/components/providers/snapshot-provider";
import { Terminal } from "@/components/workspace/terminal/terminal";
import { TickerInfoProvider } from "@/components/workspace/ticker-info-provider";
import { WorkspaceChatProviders } from "@/components/workspace/workspace-chat";
import { WorkspaceToastProvider } from "@/components/workspace/workspace-toast";
import { useIsMobile } from "@/lib/workspace/use-is-mobile";

import { RightRail } from "./right-rail";
import { Sidebar } from "./sidebar";

const FULL_BLEED_PREFIXES = ["/workspace", "/board", "/research"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isMobile = useIsMobile();
  // "/" is the workspace now (founder decision 2026-07-04) — full-bleed too.
  const fullBleed =
    pathname === "/" ||
    FULL_BLEED_PREFIXES.some((prefix) => pathname?.startsWith(prefix));

  return (
    <SnapshotProvider>
      {fullBleed ? (
        // The shared chat runtime + Terminal drawer live here (not inside a
        // page) so both survive navigation across every full-bleed route
        // (/, /board, /research) — a stream started on one page keeps running
        // when the founder switches to another. Legacy sidebar routes below
        // deliberately don't mount the terminal.
        <WorkspaceToastProvider>
          <WorkspaceChatProviders>
            <TickerInfoProvider>
              <div className="min-h-svh bg-background">{children}</div>
              {isMobile ? null : (
                // Fixed, full-viewport positioning host: the Terminal's own
                // `absolute inset-0` overlay resolves against this, keeping the
                // collapsed strip pinned to the viewport bottom on every page.
                <div className="pointer-events-none fixed inset-0 z-50">
                  <Terminal />
                </div>
              )}
            </TickerInfoProvider>
          </WorkspaceChatProviders>
        </WorkspaceToastProvider>
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
