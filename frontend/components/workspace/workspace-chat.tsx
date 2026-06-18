"use client";

import { AssistantRuntimeProvider } from "@assistant-ui/react";
import type { ReactNode } from "react";

import { Thread } from "@/components/assistant-ui/thread";
import {
  StartResearchToolUI,
  TradingWelcome,
} from "@/components/agents/trading-chat-parts";
import { ProposeOrderToolUI } from "@/components/agents/propose-order-tool-ui";
import { TradingComposerControls } from "@/components/agents/trading-composer-controls";
import { ChatSlashCommands } from "@/components/agents/chat-slash-commands";
import { ChatSettingsProvider } from "@/lib/chat-settings-context";
import { LearnedMemoriesProvider } from "@/lib/learned-memories-context";
import { useTradingChatRuntime } from "@/lib/use-trading-chat-runtime";

function WorkspaceChatProvidersInner({ children }: { children: ReactNode }) {
  const runtime = useTradingChatRuntime();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <StartResearchToolUI />
      <ProposeOrderToolUI />
      {children}
    </AssistantRuntimeProvider>
  );
}

export function WorkspaceChatProviders({ children }: { children: ReactNode }) {
  return (
    <LearnedMemoriesProvider>
      <ChatSettingsProvider>
        <WorkspaceChatProvidersInner>{children}</WorkspaceChatProvidersInner>
      </ChatSettingsProvider>
    </LearnedMemoriesProvider>
  );
}

export function WorkspaceThread() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <Thread
        components={{
          Welcome: TradingWelcome,
          ComposerControls: TradingComposerControls,
          ComposerSlashCommands: ChatSlashCommands,
        }}
      />
    </div>
  );
}
