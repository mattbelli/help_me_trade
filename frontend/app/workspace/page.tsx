"use client";

import { CommandConsole } from "@/components/workspace/command-console";
import { WorkspaceChatProviders } from "@/components/workspace/workspace-chat";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";
import { WorkspaceToastProvider } from "@/components/workspace/workspace-toast";
import { WorkspaceToolbar } from "@/components/workspace/workspace-toolbar";
import { useConsoleStore } from "@/lib/workspace/use-console-store";

export default function WorkspacePage() {
  const open = useConsoleStore((s) => s.open);
  const height = useConsoleStore((s) => s.height);
  const collapsedHeight = useConsoleStore((s) => s.collapsedHeight);
  const consoleHeight = open ? height : collapsedHeight;

  return (
    <WorkspaceToastProvider>
      <div className="-mx-5 -my-8 flex h-[calc(100svh-0px)] min-h-[640px] flex-col overflow-hidden bg-background lg:-mx-8 lg:-my-10">
        <WorkspaceToolbar />

        <div
          className="relative flex min-h-0 flex-1 flex-col"
          style={{ paddingBottom: consoleHeight }}
        >
          <WorkspaceGrid />

          <WorkspaceChatProviders>
            <CommandConsole />
          </WorkspaceChatProviders>
        </div>
      </div>
    </WorkspaceToastProvider>
  );
}
