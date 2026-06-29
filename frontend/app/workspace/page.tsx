"use client";

import { useEffect } from "react";
import { motion } from "motion/react";

import { CommandConsole } from "@/components/workspace/command-console";
import { CopilotSidebar } from "@/components/workspace/copilot-sidebar";
import { MobileWorkspace } from "@/components/workspace/mobile-workspace";
import { NotificationBanner } from "@/components/workspace/notification-banner";
import { WorkspaceChatProviders } from "@/components/workspace/workspace-chat";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";
import { WorkspaceToastProvider } from "@/components/workspace/workspace-toast";
import { WorkspaceToolbar } from "@/components/workspace/workspace-toolbar";
import { useConsoleStore } from "@/lib/workspace/use-console-store";
import { useCopilotStore } from "@/lib/workspace/use-copilot-store";
import { useIsMobile } from "@/lib/workspace/use-is-mobile";

export default function WorkspacePage() {
  const isMobile = useIsMobile();
  const collapsedHeight = useConsoleStore((s) => s.collapsedHeight);
  const open = useCopilotStore((s) => s.open);
  const width = useCopilotStore((s) => s.width);
  const hydrate = useCopilotStore((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  const dockedPadding = open ? width : 0;

  // Phones get a purpose-built bottom-tab shell instead of the desktop widget
  // grid. It still lives inside the chat providers so the Agent tab's thread
  // works. AppShell's notification stream keeps the store live for the Activity tab.
  if (isMobile) {
    return (
      <WorkspaceToastProvider>
        <WorkspaceChatProviders>
          <MobileWorkspace />
        </WorkspaceChatProviders>
      </WorkspaceToastProvider>
    );
  }

  return (
    <WorkspaceToastProvider>
      <div className="flex h-svh min-h-[640px] flex-col overflow-hidden bg-background">
        <WorkspaceToolbar />
        <NotificationBanner />

        <div
          className="relative flex min-h-0 flex-1 flex-col"
          style={{ paddingBottom: collapsedHeight }}
        >
          <WorkspaceChatProviders>
            <div className="relative min-h-0 flex-1">
              <motion.div
                className="h-full min-h-0"
                initial={false}
                animate={{ paddingRight: dockedPadding }}
                transition={{ type: "spring", stiffness: 420, damping: 36 }}
              >
                <WorkspaceGrid />
              </motion.div>
              <CopilotSidebar />
            </div>
            <CommandConsole />
          </WorkspaceChatProviders>
        </div>
      </div>
    </WorkspaceToastProvider>
  );
}
