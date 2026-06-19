"use client";

import { useState, type FC } from "react";
import { HistoryIcon, PlusIcon } from "lucide-react";
import {
  AuiIf,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAuiState,
} from "@assistant-ui/react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export function CopilotThreadHistory() {
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
          />
        }
        aria-label="Chat history"
      >
        <HistoryIcon className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        side="bottom"
        className={cn(
          "w-64 max-h-[60vh] overflow-y-auto border-[var(--border)] bg-[var(--panel)] p-1 font-sans shadow-[var(--panel-shadow)]",
        )}
      >
        <CopilotThreadHistoryPanel onSelect={() => setOpen(false)} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function CopilotThreadHistoryPanel({ onSelect }: { onSelect: () => void }) {
  return (
    <ThreadListPrimitive.Root className="flex flex-col gap-0.5">
      <ThreadListPrimitive.New asChild>
        <button
          type="button"
          onClick={onSelect}
          className={cn(
            "flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-left text-[12px] text-foreground transition-colors",
            "hover:bg-[var(--bg-hover)] focus-visible:outline-none",
          )}
        >
          <PlusIcon className="size-3.5 shrink-0" />
          New chat
        </button>
      </ThreadListPrimitive.New>
      <AuiIf condition={(s) => s.threads.isLoading}>
        <p className="px-2.5 py-2 text-[12px] text-[var(--fg-dim)]">Loading…</p>
      </AuiIf>
      <AuiIf condition={(s) => !s.threads.isLoading}>
        <CopilotThreadHistoryItems onSelect={onSelect} />
      </AuiIf>
    </ThreadListPrimitive.Root>
  );
}

const CopilotThreadHistoryItems: FC<{ onSelect: () => void }> = ({
  onSelect,
}) => {
  const threadIds = useAuiState((s) => s.threads.threadIds);

  if (threadIds.length === 0) {
    return (
      <p className="px-2.5 py-2 text-[12px] text-[var(--fg-dim)]">
        No saved chats yet.
      </p>
    );
  }

  return (
    <ThreadListPrimitive.Items>
      {() => <CopilotThreadListItem onSelect={onSelect} />}
    </ThreadListPrimitive.Items>
  );
};

const CopilotThreadListItem: FC<{ onSelect: () => void }> = ({ onSelect }) => {
  return (
    <ThreadListItemPrimitive.Root
      className={cn(
        "flex min-h-8 items-center rounded-md transition-colors",
        "hover:bg-[var(--bg-hover)] data-active:bg-[var(--bg-hover)]",
      )}
    >
      <ThreadListItemPrimitive.Trigger
        onClick={onSelect}
        className={cn(
          "flex h-full w-full min-w-0 items-center px-2.5 text-left text-[12px] text-foreground",
          "focus-visible:outline-none",
        )}
      >
        <span className="min-w-0 flex-1 truncate">
          <ThreadListItemPrimitive.Title fallback="New chat" />
        </span>
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  );
};
