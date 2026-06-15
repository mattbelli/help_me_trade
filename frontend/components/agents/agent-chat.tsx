"use client";

import { Bot, CopyIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, isToolUIPart, type UIMessage } from "ai";

import {
  Conversation,
  ConversationContent,
  ConversationDownload,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Badge } from "@/components/ui/badge";
import { API_BASE } from "@/lib/api";
import { AI_PROVIDER_OPTIONS } from "@/lib/ai-settings";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "Summarize my portfolio exposure and risk",
  "What do you remember about my trading preferences?",
  "Research AAPL momentum and key levels",
  "Any open orders or positions I should watch?",
];

function getMessageText(message: UIMessage): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

function ToolPartView({
  part,
}: {
  part: Extract<UIMessage["parts"][number], { type: string }>;
}) {
  if (!isToolUIPart(part)) return null;

  const title =
    part.type === "dynamic-tool"
      ? part.toolName
      : part.type.replace(/^tool-/, "");

  return (
    <Tool defaultOpen={part.state === "output-available"}>
      {part.type === "dynamic-tool" ? (
        <ToolHeader
          type="dynamic-tool"
          state={part.state}
          toolName={part.toolName}
          title={title}
        />
      ) : (
        <ToolHeader type={part.type} state={part.state} title={title} />
      )}
      <ToolContent>
        {"input" in part && part.input !== undefined ? (
          <ToolInput input={part.input} />
        ) : null}
        {"output" in part && part.output !== undefined ? (
          <ToolOutput
            output={part.output}
            errorText={"errorText" in part ? part.errorText : undefined}
          />
        ) : null}
      </ToolContent>
    </Tool>
  );
}

export function AgentChat() {
  const [activeProvider, setActiveProvider] = useState("mock");

  useEffect(() => {
    fetch(`${API_BASE}/api/ai/providers`)
      .then((res) => res.json())
      .then((data) => setActiveProvider(data.active_provider ?? "mock"))
      .catch(() => setActiveProvider("mock"));
  }, []);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `${API_BASE}/api/chat`,
      }),
    [],
  );

  const { messages, sendMessage, status, stop, regenerate, setMessages } =
    useChat({
      transport,
      onError: (error) => {
        console.error("Chat error:", error);
      },
    });

  const isLoading = status === "streaming" || status === "submitted";

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      const text = message.text?.trim();
      if (!text || isLoading) return;
      sendMessage({ text });
    },
    [isLoading, sendMessage],
  );

  const handleSuggestion = useCallback(
    (text: string) => {
      if (isLoading) return;
      sendMessage({ text });
    },
    [isLoading, sendMessage],
  );

  const copyMessage = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // ignore clipboard errors
    }
  }, []);

  return (
    <div className="flex h-[calc(100svh-4rem)] min-h-[560px] flex-col">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight">Agent chat</h1>
            <Badge variant="secondary" className="rounded-full text-[10px]">
              Copilot
            </Badge>
            <Badge variant="outline" className="rounded-full text-[10px] font-normal">
              {AI_PROVIDER_OPTIONS.find((p) => p.id === activeProvider)?.label ??
                activeProvider}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Streaming agent with portfolio tools, memories, and research.
          </p>
        </div>
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border/80 bg-card/30">
        <Conversation className="min-h-0 flex-1">
          <ConversationDownload messages={messages} className="z-10" />
          <ConversationContent>
            {messages.length === 0 ? (
              <ConversationEmptyState
                icon={<Bot className="size-10 opacity-60" strokeWidth={1.5} />}
                title="Trading copilot"
                description="Ask about your portfolio, memories, or kick off research."
              />
            ) : (
              messages.map((message, index) => {
                const text = getMessageText(message);
                const isLastAssistant =
                  message.role === "assistant" && index === messages.length - 1;

                return (
                  <Message key={message.id} from={message.role}>
                    <MessageContent>
                      {message.parts.map((part, partIndex) => {
                        if (part.type === "text") {
                          return (
                            <MessageResponse
                              key={`${message.id}-${partIndex}`}
                              isAnimating={
                                isLastAssistant &&
                                isLoading &&
                                partIndex === message.parts.length - 1
                              }
                            >
                              {part.text}
                            </MessageResponse>
                          );
                        }
                        if (isToolUIPart(part)) {
                          return (
                            <ToolPartView
                              key={`${message.id}-${partIndex}`}
                              part={part}
                            />
                          );
                        }
                        return null;
                      })}
                      {message.role === "assistant" &&
                        isLastAssistant &&
                        isLoading &&
                        message.parts.length === 0 && (
                          <Shimmer className="text-sm">Thinking…</Shimmer>
                        )}
                    </MessageContent>
                    {message.role === "assistant" && text && !isLoading && (
                      <MessageActions>
                        <MessageAction
                          tooltip="Copy"
                          label="Copy"
                          onClick={() => copyMessage(text)}
                        >
                          <CopyIcon className="size-3.5" />
                        </MessageAction>
                        {index === messages.length - 1 ? (
                          <MessageAction
                            tooltip="Regenerate"
                            label="Regenerate"
                            onClick={() => regenerate()}
                          >
                            <RefreshCwIcon className="size-3.5" />
                          </MessageAction>
                        ) : null}
                      </MessageActions>
                    )}
                  </Message>
                );
              })
            )}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>

        <div className="border-t border-border/80 bg-background/80 p-3 backdrop-blur-sm">
          {messages.length === 0 ? (
            <Suggestions className="mb-3">
              {SUGGESTIONS.map((s) => (
                <Suggestion
                  key={s}
                  suggestion={s}
                  onClick={handleSuggestion}
                />
              ))}
            </Suggestions>
          ) : null}

          <PromptInput onSubmit={handleSubmit} className="rounded-xl">
            <PromptInputBody>
              <PromptInputTextarea
                placeholder="Ask about positions, memories, or market research…"
                disabled={isLoading}
              />
            </PromptInputBody>
            <PromptInputFooter>
              <PromptInputTools>
                <button
                  type="button"
                  className={cn(
                    "text-xs text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => setMessages([])}
                  disabled={isLoading || messages.length === 0}
                >
                  Clear chat
                </button>
              </PromptInputTools>
              <PromptInputSubmit
                disabled={isLoading}
                status={status}
                onStop={stop}
              />
            </PromptInputFooter>
          </PromptInput>
        </div>
      </div>
    </div>
  );
}
