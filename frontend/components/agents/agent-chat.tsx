"use client";

import { Bot, CopyIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, isToolUIPart, type UIMessage } from "ai";

import { ChatSessionSidebar } from "@/components/agents/chat-session-sidebar";
import { ResearchToolOutput } from "@/components/agents/research-tool-output";
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
import { API_BASE, API_TOKEN, apiFetch } from "@/lib/api";
import { AI_PROVIDER_OPTIONS } from "@/lib/ai-settings";
import { shortModelLabel } from "@/lib/ai-models";
import { ChatModelSelect } from "@/components/agents/chat-model-select";
import {
  CHAT_MODE_LABELS,
  type ChatMode,
  type ChatSession,
  type ChatSessionMessage,
  createChatSession,
  fetchChatSession,
  fetchChatSessions,
} from "@/lib/chat-sessions";
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
  const toolPart = isToolUIPart(part) ? part : null;
  const isComplete =
    toolPart?.state === "output-available" ||
    toolPart?.state === "output-error" ||
    toolPart?.state === "output-denied";

  const [open, setOpen] = useState(Boolean(isComplete));

  useEffect(() => {
    if (isComplete) setOpen(true);
  }, [isComplete]);

  if (!toolPart) return null;

  const title =
    toolPart.type === "dynamic-tool"
      ? toolPart.toolName
      : toolPart.type.replace(/^tool-/, "");
  const toolName = title;

  return (
    <Tool open={open} onOpenChange={setOpen}>
      {toolPart.type === "dynamic-tool" ? (
        <ToolHeader
          type="dynamic-tool"
          state={toolPart.state}
          toolName={toolPart.toolName}
          title={title}
        />
      ) : (
        <ToolHeader type={toolPart.type} state={toolPart.state} title={title} />
      )}
      <ToolContent>
        {"input" in toolPart && toolPart.input !== undefined ? (
          <ToolInput input={toolPart.input} />
        ) : null}
        {"output" in toolPart && toolPart.output !== undefined ? (
          toolName === "start_research" ||
          toolName === "run_research" ||
          toolPart.type === "tool-start_research" ||
          toolPart.type === "tool-run_research" ? (
            <ResearchToolOutput
              output={toolPart.output}
              errorText={"errorText" in toolPart ? toolPart.errorText : undefined}
            />
          ) : (
            <ToolOutput
              output={toolPart.output}
              errorText={"errorText" in toolPart ? toolPart.errorText : undefined}
            />
          )
        ) : null}
      </ToolContent>
    </Tool>
  );
}

function toUiMessages(messages: ChatSessionMessage[]): UIMessage[] {
  return messages.map((m) => ({
    id: m.id,
    role: m.role as UIMessage["role"],
    parts: m.parts.map((p) =>
      p.type === "text"
        ? { type: "text" as const, text: p.text ?? "" }
        : { type: "text" as const, text: "" },
    ),
  }));
}

export function AgentChat() {
  const [activeProvider, setActiveProvider] = useState("mock");
  const [chatModel, setChatModel] = useState("");
  const [chatMode, setChatMode] = useState<ChatMode>("standard");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionTitle, setSessionTitle] = useState("New chat");
  const [sidebarRefresh, setSidebarRefresh] = useState(0);
  const [bootstrapping, setBootstrapping] = useState(true);

  useEffect(() => {
    function loadProvider() {
      apiFetch(`${API_BASE}/api/ai/providers`, { cache: "no-store" })
        .then((res) => res.json())
        .then((data) => setActiveProvider(data.active_provider ?? "mock"))
        .catch(() => setActiveProvider("mock"));
    }
    loadProvider();
    window.addEventListener("focus", loadProvider);
    return () => window.removeEventListener("focus", loadProvider);
  }, []);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `${API_BASE}/api/chat`,
        headers: API_TOKEN ? { "X-API-Token": API_TOKEN } : undefined,
        body: {
          sessionId,
          mode: chatMode,
          model: chatModel || undefined,
        },
      }),
    [sessionId, chatMode, chatModel],
  );

  const chatId = sessionId ?? `quick-${chatMode}`;

  const { messages, sendMessage, status, stop, regenerate, setMessages } =
    useChat({
      id: chatId,
      transport,
      onFinish: () => {
        if (chatMode !== "quick") {
          setSidebarRefresh((n) => n + 1);
        }
      },
      onError: (error) => {
        console.error("Chat error:", error);
      },
    });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const sessions = await fetchChatSessions();
        if (!cancelled && sessions.length > 0) {
          const latest = sessions[0];
          const detail = await fetchChatSession(latest.id);
          setSessionId(latest.id);
          setChatMode(latest.mode);
          setSessionTitle(detail.title);
          setMessages(toUiMessages(detail.messages));
          return;
        }
        const session = await createChatSession("standard");
        if (!cancelled) {
          setSessionId(session.id);
          setChatMode("standard");
          setSessionTitle(session.title);
        }
      } catch {
        if (!cancelled) {
          setChatMode("quick");
          setSessionId(null);
        }
      } finally {
        if (!cancelled) setBootstrapping(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setMessages]);

  const isLoading = status === "streaming" || status === "submitted";

  const handleNewChat = useCallback(
    (mode: ChatMode, session?: ChatSession) => {
      setChatMode(mode);
      if (mode === "quick") {
        setSessionId(null);
        setSessionTitle("Quick chat");
      } else if (session) {
        setSessionId(session.id);
        setSessionTitle(session.title);
      }
      setMessages([]);
    },
    [setMessages],
  );

  const handleSelectSession = useCallback(
    async (session: ChatSession) => {
      try {
        const detail = await fetchChatSession(session.id);
        setSessionId(session.id);
        setChatMode(session.mode);
        setSessionTitle(detail.title);
        setMessages(toUiMessages(detail.messages));
      } catch (err) {
        console.error("Failed to load session:", err);
      }
    },
    [setMessages],
  );

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      const text = message.text?.trim();
      if (!text || isLoading || bootstrapping) return;
      sendMessage({ text });
    },
    [bootstrapping, isLoading, sendMessage],
  );

  const handleSuggestion = useCallback(
    (text: string) => {
      if (isLoading || bootstrapping) return;
      sendMessage({ text });
    },
    [bootstrapping, isLoading, sendMessage],
  );

  const copyMessage = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // ignore
    }
  }, []);

  const modeLabel = CHAT_MODE_LABELS[chatMode];

  return (
    <div className="-mx-5 -my-8 flex h-[calc(100svh-0px)] min-h-[600px] flex-col lg:-mx-8 lg:-my-10">
      <div className="flex min-h-0 flex-1 overflow-hidden border-y border-border/80 bg-background">
        <ChatSessionSidebar
          activeSessionId={sessionId}
          activeMode={chatMode}
          onSelectSession={handleSelectSession}
          onNewChat={handleNewChat}
          refreshKey={sidebarRefresh}
        />

        <div className="flex min-w-0 flex-1 flex-col px-5 py-6 lg:px-8">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold tracking-tight">
                  {sessionTitle}
                </h1>
                <Badge variant="secondary" className="rounded-full text-[10px]">
                  {modeLabel.label}
                </Badge>
                <Badge
                  variant="outline"
                  className="rounded-full text-[10px] font-normal"
                >
                  {AI_PROVIDER_OPTIONS.find((p) => p.id === activeProvider)
                    ?.label ?? activeProvider}
                  {chatModel ? ` · ${shortModelLabel(chatModel)}` : ""}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                {modeLabel.description}
              </p>
            </div>
          </div>

          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border/80 bg-card/30">
            <Conversation className="min-h-0 flex-1">
              <ConversationDownload messages={messages} className="z-10" />
              <ConversationContent>
                {bootstrapping ? (
                  <ConversationEmptyState
                    title="Loading chat…"
                    description="Preparing your session."
                  />
                ) : messages.length === 0 ? (
                  <ConversationEmptyState
                    icon={
                      <Bot className="size-10 opacity-60" strokeWidth={1.5} />
                    }
                    title="Trading copilot"
                    description="Ask about your portfolio, memories, or kick off research."
                  />
                ) : (
                  messages.map((message, index) => {
                    const text = getMessageText(message);
                    const isLastAssistant =
                      message.role === "assistant" &&
                      index === messages.length - 1;

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
              {messages.length === 0 && !bootstrapping ? (
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
                    disabled={isLoading || bootstrapping}
                  />
                </PromptInputBody>
                <PromptInputFooter>
                  <PromptInputTools>
                    <ChatModelSelect
                      key={activeProvider}
                      provider={activeProvider}
                      value={chatModel}
                      onChange={setChatModel}
                      disabled={isLoading || bootstrapping}
                    />
                    <span className="hidden text-xs text-muted-foreground sm:inline">
                      {chatMode === "standard"
                        ? "Mem0 enabled"
                        : chatMode === "private"
                          ? "Private — not saved to memory"
                          : "Quick — ephemeral"}
                    </span>
                  </PromptInputTools>
                  <PromptInputSubmit
                    disabled={isLoading || bootstrapping}
                    status={status}
                    onStop={stop}
                  />
                </PromptInputFooter>
              </PromptInput>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
