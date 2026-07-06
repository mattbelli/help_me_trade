"use client";

import { useState } from "react";
import { useAuiState } from "@assistant-ui/react";
import { Check, Pencil, Sparkles, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useLearnedMemories } from "@/lib/learned-memories-context";
import {
  MEMORY_TYPE_LABELS,
  archiveMemory,
  updateMemory,
  type MemoryRecord,
} from "@/lib/memories";

export function TurnLearnedMemories() {
  const messageId = useAuiState((s) => s.message.id) as string | undefined;
  const { byMessage, removeLearnedMemory, updateLearnedMemory } =
    useLearnedMemories();
  const items = messageId ? byMessage[messageId] : undefined;
  if (!messageId || !items || items.length === 0) return null;

  return (
    <div className="mt-2 ms-2 flex flex-col gap-1.5 rounded-lg border border-border/60 bg-muted/30 p-2.5">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        <Sparkles className="size-3.5" />
        Learned from this chat
      </div>
      <ul className="flex flex-col gap-1.5">
        {items.map((m) => (
          <LearnedChip
            key={m.id}
            memory={m}
            onDiscarded={() => removeLearnedMemory(messageId, m.id)}
            onUpdated={(updated) => updateLearnedMemory(messageId, updated)}
          />
        ))}
      </ul>
    </div>
  );
}

function LearnedChip({
  memory,
  onDiscarded,
  onUpdated,
}: {
  memory: MemoryRecord;
  onDiscarded: () => void;
  onUpdated: (m: MemoryRecord) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(memory.content);
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!draft.trim() || busy) return;
    setBusy(true);
    try {
      const updated = await updateMemory(memory.id, { content: draft.trim() });
      onUpdated(updated);
      setEditing(false);
    } catch {
      /* keep editing open on failure */
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    if (busy) return;
    setBusy(true);
    try {
      // Undo = archive (restorable from the widget's History), never a hard delete.
      await archiveMemory(memory.id);
      onDiscarded();
    } catch {
      setBusy(false);
    }
  }

  return (
    <li className="flex items-start gap-2 text-xs">
      <span className="mt-0.5 shrink-0 rounded border border-border/60 bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        {MEMORY_TYPE_LABELS[memory.type]}
      </span>
      {editing ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
          autoFocus
          className="min-w-0 flex-1 resize-none rounded-md border border-border bg-background px-2 py-1 text-xs outline-none"
        />
      ) : (
        <span className="min-w-0 flex-1 text-foreground/90">
          {memory.content}
        </span>
      )}
      <div className="flex shrink-0 items-center gap-0.5">
        {editing ? (
          <>
            <Button
              size="icon"
              variant="ghost"
              className="size-6"
              onClick={save}
              disabled={busy}
              aria-label="Save memory"
            >
              <Check className="size-3.5" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-6"
              onClick={() => {
                setEditing(false);
                setDraft(memory.content);
              }}
              aria-label="Cancel edit"
            >
              <X className="size-3.5" />
            </Button>
          </>
        ) : (
          <>
            <Button
              size="icon"
              variant="ghost"
              className="size-6"
              onClick={() => setEditing(true)}
              aria-label="Edit memory"
            >
              <Pencil className="size-3.5" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-6 text-muted-foreground hover:text-destructive"
              onClick={discard}
              disabled={busy}
              aria-label="Undo — archive memory"
              title="Undo (archive — restorable from History)"
            >
              <X className="size-3.5" />
            </Button>
          </>
        )}
      </div>
    </li>
  );
}
