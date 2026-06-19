"use client";

import { useState, type FC } from "react";
import {
  MoreHorizontalIcon,
  PencilIcon,
  PinIcon,
  PlusIcon,
  TrashIcon,
} from "lucide-react";
import {
  AuiIf,
  ThreadListItemMorePrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { useAui } from "@assistant-ui/store";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { setChatSessionPinned } from "@/lib/chat-sessions";

export function CopilotThreadHistory({
  onAfterSelect,
}: {
  onAfterSelect: () => void;
}) {
  return (
    <ThreadListPrimitive.Root className="flex flex-col gap-0.5 p-2 font-sans">
      <ThreadListPrimitive.New asChild>
        <button
          type="button"
          onClick={onAfterSelect}
          className={cn(
            "flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-left text-[12px] text-foreground transition-colors",
            "hover:bg-[var(--bg-hover)] focus-visible:outline-none",
          )}
        >
          <PlusIcon className="size-3.5 shrink-0" />
          ＋ New chat
        </button>
      </ThreadListPrimitive.New>
      <AuiIf condition={(s) => s.threads.isLoading}>
        <p className="px-2.5 py-2 text-[12px] text-[var(--fg-dim)]">Loading…</p>
      </AuiIf>
      <AuiIf condition={(s) => !s.threads.isLoading}>
        <CopilotThreadHistoryItems onAfterSelect={onAfterSelect} />
      </AuiIf>
    </ThreadListPrimitive.Root>
  );
}

const CopilotThreadHistoryItems: FC<{ onAfterSelect: () => void }> = ({
  onAfterSelect,
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
      {() => <CopilotThreadListItem onAfterSelect={onAfterSelect} />}
    </ThreadListPrimitive.Items>
  );
};

const CopilotThreadListItem: FC<{ onAfterSelect: () => void }> = ({
  onAfterSelect,
}) => {
  const pinned = useAuiState((s) => Boolean(s.threadListItem.custom?.pinned));

  return (
    <ThreadListItemPrimitive.Root
      className={cn(
        "group relative flex min-h-8 items-center rounded-md transition-colors",
        "hover:bg-[var(--bg-hover)] data-active:bg-[var(--bg-hover)]",
      )}
    >
      <ThreadListItemPrimitive.Trigger
        onClick={onAfterSelect}
        className={cn(
          "flex h-full w-full min-w-0 items-center gap-1.5 px-2.5 text-left text-[12px] text-foreground",
          "group-hover:pe-9 group-has-data-[state=open]:pe-9 group-data-active:pe-9",
          "focus-visible:outline-none",
        )}
      >
        {pinned ? (
          <PinIcon
            className="size-3 shrink-0 text-[var(--fg-dim)] opacity-60"
            aria-hidden
          />
        ) : null}
        <span className="min-w-0 flex-1 truncate">
          <ThreadListItemPrimitive.Title fallback="New chat" />
        </span>
      </ThreadListItemPrimitive.Trigger>
      <CopilotThreadListItemMore />
    </ThreadListItemPrimitive.Root>
  );
};

const CopilotThreadListItemMore: FC = () => {
  const aui = useAui();
  const title = useAuiState((s) => s.threadListItem.title);
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const pinned = useAuiState((s) => Boolean(s.threadListItem.custom?.pinned));
  const [renameOpen, setRenameOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [pinning, setPinning] = useState(false);

  function openRename() {
    setDraft(title?.trim() || "");
    setRenameOpen(true);
  }

  async function handleRename() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    setSaving(true);
    try {
      await aui.threadListItem().rename(trimmed);
      setRenameOpen(false);
    } finally {
      setSaving(false);
    }
  }

  async function handlePinToggle() {
    if (!remoteId || pinning) return;
    setPinning(true);
    try {
      await setChatSessionPinned(remoteId, !pinned);
      await aui.threads().reload();
    } finally {
      setPinning(false);
    }
  }

  return (
    <>
      <ThreadListItemMorePrimitive.Root>
        <ThreadListItemMorePrimitive.Trigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className={cn(
              "absolute end-1 top-1/2 size-6 -translate-y-1/2 p-0 opacity-0 transition-opacity",
              "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
              "group-hover:opacity-100 group-data-active:opacity-100 data-[state=open]:opacity-100",
              "data-[state=open]:bg-[var(--bg-hover)] data-[state=open]:text-foreground",
            )}
          >
            <MoreHorizontalIcon className="size-3.5" />
            <span className="sr-only">More options</span>
          </Button>
        </ThreadListItemMorePrimitive.Trigger>
        <ThreadListItemMorePrimitive.Content
          side="right"
          align="start"
          sideOffset={6}
          className={cn(
            "z-50 min-w-[8rem] overflow-hidden rounded-lg border border-[var(--border)] p-1 font-sans shadow-[var(--panel-shadow)] backdrop-blur-sm",
            "bg-[var(--panel)]/95 text-foreground",
          )}
        >
          <ThreadListItemMorePrimitive.Item
            className={cn(
              "flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-[12px] outline-none select-none",
              "hover:bg-[var(--bg-hover)] focus:bg-[var(--bg-hover)]",
            )}
            onClick={openRename}
          >
            <PencilIcon className="size-3.5" />
            Rename
          </ThreadListItemMorePrimitive.Item>
          <ThreadListItemMorePrimitive.Item
            className={cn(
              "flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-[12px] outline-none select-none",
              "hover:bg-[var(--bg-hover)] focus:bg-[var(--bg-hover)]",
              pinning && "pointer-events-none opacity-50",
            )}
            onClick={() => void handlePinToggle()}
          >
            <PinIcon className="size-3.5" />
            {pinned ? "Unpin" : "Pin"}
          </ThreadListItemMorePrimitive.Item>
          <ThreadListItemPrimitive.Delete asChild>
            <ThreadListItemMorePrimitive.Item
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-[12px] outline-none select-none",
                "text-[var(--c-red)] hover:bg-[var(--bg-hover)] focus:bg-[var(--bg-hover)]",
              )}
            >
              <TrashIcon className="size-3.5" />
              Delete
            </ThreadListItemMorePrimitive.Item>
          </ThreadListItemPrimitive.Delete>
        </ThreadListItemMorePrimitive.Content>
      </ThreadListItemMorePrimitive.Root>

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent showCloseButton className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename chat</DialogTitle>
          </DialogHeader>
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Chat title"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleRename();
            }}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setRenameOpen(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => void handleRename()}
              disabled={saving || !draft.trim()}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
};
