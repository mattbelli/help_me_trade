"use client";

import {
  LockIcon,
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

function ModeIcon({ mode }: { mode: ChatMode }) {
  if (mode === "private") return <LockIcon className="size-3" />;
  if (mode === "quick") return <ZapIcon className="size-3" />;
  return <MessageSquarePlusIcon className="size-3" />;
}

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

  async function handleNew(mode: Exclude<ChatMode, "quick">) {
    setCreating(true);
    try {
      const session = await createChatSession(mode);
      setSessions((prev) => [session, ...prev.filter((s) => s.id !== session.id)]);
      onNewChat(mode, session);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(sessionId: string, e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await deleteChatSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (activeSessionId === sessionId) {
        onNewChat("quick");
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
            <DropdownMenuItem onClick={() => handleNew("standard")}>
              <div className="flex flex-col gap-0.5">
                <span className="font-medium">{CHAT_MODE_LABELS.standard.label}</span>
                <span className="text-xs text-muted-foreground">
                  {CHAT_MODE_LABELS.standard.description}
                </span>
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleNew("private")}>
              <div className="flex flex-col gap-0.5">
                <span className="font-medium">{CHAT_MODE_LABELS.private.label}</span>
                <span className="text-xs text-muted-foreground">
                  {CHAT_MODE_LABELS.private.description}
                </span>
              </div>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => onNewChat("quick")}>
              <div className="flex flex-col gap-0.5">
                <span className="flex items-center gap-1.5 font-medium">
                  <ZapIcon className="size-3.5" />
                  {CHAT_MODE_LABELS.quick.label}
                </span>
                <span className="text-xs text-muted-foreground">
                  {CHAT_MODE_LABELS.quick.description}
                </span>
              </div>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {activeMode === "quick" && !activeSessionId ? (
          <div className="mb-2 rounded-md border border-dashed border-border/80 px-2 py-2 text-xs text-muted-foreground">
            <div className="flex items-center gap-1.5 font-medium text-foreground">
              <ZapIcon className="size-3" />
              Quick chat active
            </div>
            <p className="mt-1">Not saved to sidebar or memory.</p>
          </div>
        ) : null}

        {loading ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">Loading…</p>
        ) : sessions.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            No saved chats yet. Start a standard or private chat.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((session) => {
              const active = session.id === activeSessionId;
              return (
                <li key={session.id}>
                  <button
                    type="button"
                    onClick={() => onSelectSession(session)}
                    className={cn(
                      "group flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors",
                      active
                        ? "bg-muted text-foreground"
                        : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                    )}
                  >
                    <ModeIcon mode={session.mode} />
                    <span className="min-w-0 flex-1 truncate leading-snug">
                      {session.title}
                    </span>
                    <button
                      type="button"
                      aria-label="Delete chat"
                      className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                      onClick={(e) => handleDelete(session.id, e)}
                    >
                      <Trash2Icon className="size-3.5 text-muted-foreground hover:text-destructive" />
                    </button>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
