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
import { usePageHotkeys } from "@/lib/workspace/use-page-hotkeys";

export default function WorkspacePage() {
  usePageHotkeys();

  const isMobile = useIsMobile();
  const collapsedHeight = useConsoleStore((s) => s.collapsedHeight);
  const open = useCopilotStore((s) => s.open);
  const pinned = useCopilotStore((s) => s.pinned);
  const width = useCopilotStore((s) => s.width);
  const hydrate = useCopilotStore((s) => s.hydrate);
  const toggleCopilot = useCopilotStore((s) => s.toggle);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  // Ctrl/Cmd+J toggles the copilot open/closed — doesn't collide with the
  // command console's Ctrl/Cmd+K or the Alt+1-5 / Alt+[ / ] page hotkeys.
  useEffect(() => {
    if (isMobile) return;

    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        toggleCopilot();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isMobile, toggleCopilot]);

  // Only a PINNED (docked) copilot should push the grid over — a floating
  // copilot overlays the grid instead, so it must not reserve layout space.
  const dockedPadding = open && pinned ? width : 0;

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
