"use client";

import { AssistantRuntimeProvider } from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/thread";
import {
  StartResearchToolUI,
  TradingWelcome,
} from "@/components/agents/trading-chat-parts";
import { ProposeOrderToolUI } from "@/components/agents/propose-order-tool-ui";
import { ProposeRuleToolUI } from "@/components/agents/propose-rule-tool-ui";
import { TradingComposerControls } from "@/components/agents/trading-composer-controls";
import { ChatSlashCommands } from "@/components/agents/chat-slash-commands";
import { TradingThreadList } from "@/components/agents/trading-thread-list";
import { ChatSettingsProvider } from "@/lib/chat-settings-context";
import { LearnedMemoriesProvider } from "@/lib/learned-memories-context";
import { useTradingChatRuntime } from "@/lib/use-trading-chat-runtime";

function TradingChatInner() {
  const runtime = useTradingChatRuntime();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <StartResearchToolUI />
      <ProposeOrderToolUI />
      <ProposeRuleToolUI />
      <div className="-mx-5 -my-8 flex h-[calc(100svh-0px)] min-h-[600px] overflow-hidden border-y border-border/80 bg-background lg:-mx-8 lg:-my-10">
        <aside className="flex w-56 shrink-0 flex-col border-r border-border/80 bg-card/20 pt-10 pl-1">
          <div className="flex-1 overflow-y-auto p-2 pt-0">
            <TradingThreadList />
          </div>
        </aside>
        <main className="min-h-0 min-w-0 flex-1">
          <Thread
            components={{
              Welcome: TradingWelcome,
              ComposerControls: TradingComposerControls,
              ComposerSlashCommands: ChatSlashCommands,
            }}
          />
        </main>
      </div>
    </AssistantRuntimeProvider>
  );
}

export function TradingChat() {
  return (
    <LearnedMemoriesProvider>
      <ChatSettingsProvider>
        <TradingChatInner />
      </ChatSettingsProvider>
    </LearnedMemoriesProvider>
  );
}
