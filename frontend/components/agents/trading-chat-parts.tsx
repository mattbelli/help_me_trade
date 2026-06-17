"use client";

import type { FC } from "react";
import { makeAssistantToolUI } from "@assistant-ui/react";

import { ResearchToolOutput } from "@/components/agents/research-tool-output";

export const StartResearchToolUI = makeAssistantToolUI<
  Record<string, unknown>,
  unknown
>({
  toolName: "start_research",
  render: ({ result }) => <ResearchToolOutput output={result} />,
});

export const TradingWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-8 flex flex-col items-center text-center">
      <h2 className="text-2xl font-semibold tracking-tight">
        Trading copilot
      </h2>
      <p className="text-muted-foreground mt-2 max-w-md text-sm">
        Type <span className="font-mono text-xs">/</span> for commands, or enable
        tools in the composer when you want data pulled in.
      </p>
    </div>
  );
};
