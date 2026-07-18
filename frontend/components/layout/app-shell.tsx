"use client";

import { ExecutionMonitor } from "@/components/trading/execution-monitor";
import { PriceAlertMonitor } from "@/components/trading/price-alert-monitor";
import { SnapshotProvider } from "@/components/providers/snapshot-provider";
import { Terminal } from "@/components/workspace/terminal/terminal";
import { TickerInfoProvider } from "@/components/workspace/ticker-info-provider";
import { WorkspaceChatProviders } from "@/components/workspace/workspace-chat";
import { WorkspaceToastProvider } from "@/components/workspace/workspace-toast";
import { useIsMobile } from "@/lib/workspace/use-is-mobile";

export function AppShell({ children }: { children: React.ReactNode }) {
  const isMobile = useIsMobile();

  // ONE UI world (founder order 2026-07-18): every route is full-bleed
  // workspace chrome. The legacy sidebar shell is gone; old routes redirect
  // to "/". The shared chat runtime + Terminal drawer live here (not inside
  // a page) so both survive navigation across routes — a stream started on
  // one page keeps running when the founder switches to another.
  return (
    <SnapshotProvider>
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
      <PriceAlertMonitor />
      <ExecutionMonitor />
    </SnapshotProvider>
  );
}
