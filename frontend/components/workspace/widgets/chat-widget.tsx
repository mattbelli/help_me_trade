"use client";

import type { FC } from "react";

import { Thread } from "@/components/assistant-ui/thread";

const ChatWidgetWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-4 flex flex-col px-3 py-2">
      <p className="font-mono text-[11px] text-[var(--fg-dim)]">
        Ask your copilot — type in the console below
      </p>
    </div>
  );
};

export function ChatWidget() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--bg-inset)]">
      <Thread messagesOnly components={{ Welcome: ChatWidgetWelcome }} />
    </div>
  );
}
