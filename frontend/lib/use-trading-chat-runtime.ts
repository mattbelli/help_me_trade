"use client";

import { useChat } from "@ai-sdk/react";
import type { AssistantRuntime } from "@assistant-ui/core";
import { useRemoteThreadListRuntime } from "@assistant-ui/react";
import {
  AssistantChatTransport,
  useAISDKRuntime,
} from "@assistant-ui/react-ai-sdk";
import { useAui, useAuiState } from "@assistant-ui/store";
import {
  lastAssistantMessageIsCompleteWithApprovalResponses,
  lastAssistantMessageIsCompleteWithToolCalls,
  type UIMessage,
} from "ai";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { useChatSettings } from "@/lib/chat-settings-context";
import { useLearnedMemories } from "@/lib/learned-memories-context";
import { extractTurnMemories, listSessionMemories } from "@/lib/memories";
import { usePythonThreadListAdapter } from "@/lib/python-thread-adapter";

const SUGGESTIONS = [
  { prompt: "What can you help me with?" },
  { prompt: "What do you remember about my trading preferences?" },
  { prompt: "Walk me through how to use the research tool" },
  { prompt: "Explain the difference between realized P&L and cash flow" },
];

function messageText(m: UIMessage): string {
  return m.parts
    .filter((p) => p.type === "text")
    .map((p) => ("text" in p ? p.text : ""))
    .join("\n")
    .trim();
}

function useChatThreadRuntime(): AssistantRuntime {
  const { settingsRef } = useChatSettings();
  const { setLearnedForMessage, clearLearnedMemories } = useLearnedMemories();

  const transport = useMemo(
    () =>
      new AssistantChatTransport({
        api: "/api/chat",
        body: () => ({
          mode: "saved",
          model: settingsRef.current.model || undefined,
          enabledTools: settingsRef.current.enabledTools,
          autoInjectMemories: settingsRef.current.autoInjectMemories,
        }),
      }),
    [settingsRef],
  );

  const id = useAuiState((s) => s.threadListItem.id);
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const remoteIdRef = useRef(remoteId);
  useEffect(() => {
    remoteIdRef.current = remoteId;
  }, [remoteId]);

  const extractedForMessageRef = useRef<Set<string>>(new Set());

  const onFinish = useCallback(
    (opts: { message: UIMessage; messages: UIMessage[] }) => {
      const msgs = opts.messages;
      const assistant = opts.message;
      if (!assistant) return;
      if (extractedForMessageRef.current.has(assistant.id)) return;
      const assistantText = messageText(assistant);
      const userMsg = [...msgs].reverse().find((m) => m.role === "user");
      const userText = userMsg ? messageText(userMsg) : "";
      if (!assistantText || !userText) return;
      extractedForMessageRef.current.add(assistant.id);
      extractTurnMemories({
        sessionId: remoteIdRef.current,
        sourceMessageId: assistant.id,
        userText,
        assistantText,
        model: settingsRef.current.model || undefined,
      })
        .then((memories) => {
          if (memories.length) setLearnedForMessage(assistant.id, memories);
        })
        .catch(() => {
          extractedForMessageRef.current.delete(assistant.id);
        });
    },
    [setLearnedForMessage, settingsRef],
  );

  useEffect(() => {
    if (!remoteId) {
      clearLearnedMemories();
      extractedForMessageRef.current.clear();
      return;
    }

    let cancelled = false;
    const sessionId = remoteId;
    extractedForMessageRef.current.clear();
    clearLearnedMemories();

    listSessionMemories(sessionId)
      .then((memories) => {
        if (cancelled) return;
        const grouped = new Map<string, typeof memories>();
        for (const memory of memories) {
          const messageId = memory.sourceMessageId;
          if (!messageId) continue;
          const existing = grouped.get(messageId);
          if (existing) existing.push(memory);
          else grouped.set(messageId, [memory]);
        }
        for (const [messageId, mems] of grouped) {
          if (extractedForMessageRef.current.has(messageId)) continue;
          setLearnedForMessage(messageId, mems);
        }
      })
      .catch(() => {
        // Leave chips empty; user can still chat.
      });

    return () => {
      cancelled = true;
    };
  }, [remoteId, clearLearnedMemories, setLearnedForMessage]);

  const aui = useAui();
  const chat = useChat({
    id,
    transport,
    onFinish,
    sendAutomaticallyWhen: (options) =>
      lastAssistantMessageIsCompleteWithApprovalResponses(options) ||
      lastAssistantMessageIsCompleteWithToolCalls(options),
  });

  const runtime = useAISDKRuntime(chat, {
    suggestions: SUGGESTIONS,
  });

  if (transport instanceof AssistantChatTransport) {
    transport.setRuntime(runtime);
    transport.__internal_setGetThreadListItem(() =>
      aui.threadListItem.source ? aui.threadListItem() : undefined,
    );
  }

  return runtime;
}

export function useTradingChatRuntime(): AssistantRuntime {
  const adapter = usePythonThreadListAdapter();

  return useRemoteThreadListRuntime({
    adapter,
    runtimeHook: function TradingRuntimeHook() {
      return useChatThreadRuntime();
    },
    allowNesting: true,
  });
}
