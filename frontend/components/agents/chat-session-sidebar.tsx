"use client";

import {
  MessageSquarePlusIcon,
  Trash2Icon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CHAT_MODE_LABELS,
  type ChatMode,
  type ChatSession,
  createChatSession,
  deleteChatSession,
  fetchChatSessions,
} from "@/lib/chat-sessions";
import { cn } from "@/lib/utils";

type Props = {
  activeSessionId: string | null;
  activeMode: ChatMode;
  onSelectSession: (session: ChatSession) => void;
  onNewChat: (mode: ChatMode, session?: ChatSession) => void;
  refreshKey?: number;
};

export function ChatSessionSidebar({
  activeSessionId,
  activeMode,
  onSelectSession,
  onNewChat,
  refreshKey = 0,
}: Props) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchChatSessions();
      setSessions(list);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions, refreshKey]);

  async function handleNewSaved() {
    setCreating(true);
    try {
      const session = await createChatSession();
      setSessions((prev) => [session, ...prev.filter((s) => s.id !== session.id)]);
      onNewChat("saved", session);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(sessionId: string, e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    try {
      await deleteChatSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (activeSessionId === sessionId) {
        onNewChat("temporary");
      }
    } catch {
      // ignore
    }
  }

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border/80 bg-card/20">
      <div className="border-b border-border/80 p-3">
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                className="w-full justify-start gap-2"
                size="sm"
                disabled={creating}
              />
            }
          >
            <MessageSquarePlusIcon className="size-4" />
            New chat
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64">
            <DropdownMenuLabel>Chat mode</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => handleNewSaved()}>
              <div className="flex flex-col gap-0.5">
                <span className="font-medium">{CHAT_MODE_LABELS.saved.label}</span>
                <span className="text-xs text-muted-foreground">
                  {CHAT_MODE_LABELS.saved.description}
                </span>
              </div>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => onNewChat("temporary")}>
              <div className="flex flex-col gap-0.5">
                <span className="flex items-center gap-1.5 font-medium">
                  <ZapIcon className="size-3.5" />
                  {CHAT_MODE_LABELS.temporary.label}
                </span>
                <span className="text-xs text-muted-foreground">
                  {CHAT_MODE_LABELS.temporary.description}
                </span>
              </div>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {activeMode === "temporary" && !activeSessionId ? (
          <div className="mb-2 rounded-md border border-dashed border-border/80 px-2 py-2 text-xs text-muted-foreground">
            <div className="flex items-center gap-1.5 font-medium text-foreground">
              <ZapIcon className="size-3" />
              Temporary chat active
            </div>
            <p className="mt-1">Not saved to sidebar or memory.</p>
          </div>
        ) : null}

        {loading ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">Loading…</p>
        ) : sessions.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            No saved chats yet. Start a saved chat.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((session) => {
              const active = session.id === activeSessionId;
              const isPrivate = session.mode === "private";
              return (
                <li key={session.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelectSession(session)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectSession(session);
                      }
                    }}
                    className={cn(
                      "group flex w-full cursor-pointer items-start gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors",
                      active
                        ? "bg-muted text-foreground"
                        : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                    )}
                  >
                    <MessageSquarePlusIcon className="mt-0.5 size-3 shrink-0" />
                    <span className="min-w-0 flex-1 truncate leading-snug">
                      {session.title}
                      {isPrivate ? (
                        <span className="ml-1 text-[10px] text-muted-foreground">
                          (private)
                        </span>
                      ) : null}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Delete chat"
                      className="size-7 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                      onClick={(e) => handleDelete(session.id, e)}
                    >
                      <Trash2Icon className="size-3.5 text-muted-foreground hover:text-destructive" />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
