"use client";

import {
  type FC,
  type PropsWithChildren,
  useCallback,
  useMemo,
  useState,
} from "react";
import type { UIMessage } from "ai";
import type {
  MessageFormatAdapter,
  MessageFormatItem,
  MessageFormatRepository,
  RemoteThreadInitializeResponse,
  RemoteThreadListAdapter,
  RemoteThreadListResponse,
  RemoteThreadMetadata,
  ThreadHistoryAdapter,
} from "@assistant-ui/core";
import {
  createSimpleTitleAdapter,
  RuntimeAdapterProvider,
} from "@assistant-ui/core/react";
import { useAui } from "@assistant-ui/store";
import { createAssistantStream } from "assistant-stream";

import {
  createChatSession,
  deleteChatSession,
  fetchChatSession,
  fetchChatSessions,
  renameChatSession,
} from "@/lib/chat-sessions";
import { toUiMessage } from "@/lib/chat-message-parts";

class PythonThreadHistoryAdapter implements ThreadHistoryAdapter {
  constructor(private aui: ReturnType<typeof useAui>) {}

  withFormat<TMessage, TStorageFormat extends Record<string, unknown>>(
    formatAdapter: MessageFormatAdapter<TMessage, TStorageFormat>,
  ) {
    const adapter = this;
    return {
      async load(): Promise<MessageFormatRepository<TMessage>> {
        const remoteId = adapter.aui.threadListItem().getState().remoteId;
        if (!remoteId) return { messages: [], headId: null };

        const detail = await fetchChatSession(remoteId);
        let parentId: string | null = null;
        const items: MessageFormatItem<TMessage>[] = [];

        for (const message of detail.messages) {
          const ui = toUiMessage(message);
          const encoded = formatAdapter.encode({
            parentId,
            message: ui as TMessage,
          });
          items.push(
            formatAdapter.decode({
              id: ui.id,
              parent_id: parentId,
              format: formatAdapter.format,
              content: encoded,
            }),
          );
          parentId = ui.id;
        }

        return { messages: items, headId: parentId };
      },
      async append() {
        // Python /api/chat/finalize persists turns after streaming.
      },
    };
  }

  async load() {
    return { messages: [] };
  }

  async append() {
    // Handled by Python finalize.
  }
}

function createHistoryProvider(): FC<PropsWithChildren> {
  const Provider: FC<PropsWithChildren> = ({ children }) => {
    const aui = useAui();
    const history = useMemo(() => new PythonThreadHistoryAdapter(aui), [aui]);
    const adapters = useMemo(() => ({ history }), [history]);
    return (
      <RuntimeAdapterProvider adapters={adapters}>
        {children}
      </RuntimeAdapterProvider>
    );
  };
  return Provider;
}

export function usePythonThreadListAdapter(): RemoteThreadListAdapter {
  const unstable_Provider = useCallback(createHistoryProvider(), []);

  return useMemo(
    (): RemoteThreadListAdapter => ({
      unstable_Provider,

      async list(): Promise<RemoteThreadListResponse> {
        const sessions = await fetchChatSessions();
        return {
          threads: sessions.map((session) => ({
            status: "regular" as const,
            remoteId: session.id,
            externalId: session.id,
            title: session.title,
            lastMessageAt: new Date(session.updated_at),
            custom: { pinned: Boolean(session.pinned) },
          })),
        };
      },

      async initialize(
        _threadId: string,
      ): Promise<RemoteThreadInitializeResponse> {
        const session = await createChatSession();
        return {
          remoteId: session.id,
          externalId: session.id,
        };
      },

      async fetch(threadId: string): Promise<RemoteThreadMetadata> {
        const sessions = await fetchChatSessions();
        const session = sessions.find((s) => s.id === threadId);
        if (!session) {
          throw new Error("Thread not found");
        }
        return {
          status: "regular",
          remoteId: session.id,
          externalId: session.id,
          title: session.title,
          lastMessageAt: new Date(session.updated_at),
          custom: { pinned: Boolean(session.pinned) },
        };
      },

      async rename(remoteId: string, newTitle: string): Promise<void> {
        await renameChatSession(remoteId, newTitle);
      },

      async archive(): Promise<void> {},

      async unarchive(): Promise<void> {},

      async delete(remoteId: string): Promise<void> {
        await deleteChatSession(remoteId);
      },

      async generateTitle(remoteId, messages) {
        const title = await createSimpleTitleAdapter().generateTitle(messages);
        await renameChatSession(remoteId, title);
        return createAssistantStream((controller) => {
          controller.appendText(title);
        });
      },
    }),
    [unstable_Provider],
  );
}
