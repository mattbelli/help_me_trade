"use client";

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type HistoryThread = {
  /** remoteId when persisted, else the local thread id — both resolve in the runtime. */
  id: string;
  title: string;
  lastMessageAt?: Date;
};

function relTime(d?: Date): string {
  if (!d) return "";
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const days = Math.floor(h / 24);
  if (days < 7) return `${days}d`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Quake-style history sidebar that slides out from the drawer's left edge and
 * overlays the transcript (no reflow). Presentational: the terminal owns the
 * runtime (switch/rename/delete/new) and passes callbacks. Transient — closed
 * whenever the drawer closes.
 */
export function TerminalHistorySidebar({
  open,
  threads,
  activeId,
  reduce,
  hamburgerRef,
  onClose,
  onSelectThread,
  onNewChat,
  onRename,
  onDelete,
}: {
  open: boolean;
  threads: HistoryThread[];
  activeId: string | null;
  reduce: boolean;
  hamburgerRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onSelectThread: (id: string) => void;
  onNewChat: () => void;
  onRename: (id: string, title: string) => void | Promise<void>;
  onDelete: (id: string) => void | Promise<void>;
}) {
  const asideRef = useRef<HTMLElement>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  // Reset any inline edit/confirm whenever the sidebar closes (render-time, so
  // it never lingers into the next open).
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (!open) {
      if (editingId) setEditingId(null);
      if (confirmingId) setConfirmingId(null);
    }
  }

  // Esc ordering (capture phase, so it beats the drawer's own Esc handling):
  // cancel an active rename, else cancel a delete-confirm, else close the
  // sidebar. A second Esc — once the sidebar is gone — falls through to the
  // drawer's close handler.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (editingId) {
        e.stopPropagation();
        setEditingId(null);
        return;
      }
      if (confirmingId) {
        e.stopPropagation();
        setConfirmingId(null);
        return;
      }
      e.stopPropagation();
      onClose();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, editingId, confirmingId, onClose]);

  // Click-away: a pointerdown outside the sidebar (and not on the ☰ toggle)
  // closes it. Interactions INSIDE — rename input, delete confirm — are
  // contained and never close it (the old switcher's click-away lesson).
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: globalThis.PointerEvent) => {
      if (!(e.target instanceof Node)) return;
      if (asideRef.current?.contains(e.target)) return;
      if (hamburgerRef.current?.contains(e.target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () =>
      document.removeEventListener("pointerdown", onPointerDown, true);
  }, [open, hamburgerRef, onClose]);

  function beginRename(t: HistoryThread) {
    setConfirmingId(null);
    setDraft(t.title);
    setEditingId(t.id);
  }

  async function commitRename(id: string) {
    const trimmed = draft.trim();
    setEditingId(null);
    if (trimmed) await onRename(id, trimmed);
  }

  function onRenameKeyDown(
    e: KeyboardEvent<HTMLInputElement>,
    id: string,
  ) {
    if (e.key === "Enter") {
      e.preventDefault();
      void commitRename(id);
    }
  }

  async function confirmDelete(id: string) {
    setConfirmingId(null);
    await onDelete(id);
  }

  return (
    <aside
      ref={asideRef}
      aria-label="Chat history"
      aria-hidden={!open}
      className="absolute inset-y-0 left-0 z-20 flex flex-col border-r border-[var(--border)] bg-[var(--bg-raised)] shadow-lg"
      style={{
        width: "min(240px, 85%)",
        transform: open ? "translateX(0)" : "translateX(-100%)",
        transition: reduce ? "none" : "transform 120ms ease-out",
        pointerEvents: open ? "auto" : "none",
        fontFamily: "var(--font-terminal)",
      }}
    >
      <button
        type="button"
        onClick={onNewChat}
        className="flex shrink-0 items-center gap-1.5 px-3 py-2 text-left text-[length:var(--fs-body)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
      >
        <Plus className="size-3.5 shrink-0" />
        New chat
      </button>
      <div className="mx-3 border-t border-[var(--border)]" />
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {threads.length === 0 ? (
          <p className="px-3 py-2 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
            No saved chats yet.
          </p>
        ) : (
          threads.map((t) => {
            const active = t.id === activeId;
            const editing = editingId === t.id;
            const confirming = confirmingId === t.id;
            return (
              <div
                key={t.id}
                className={cn(
                  "group/row relative flex items-center gap-1 pr-1.5 text-[length:var(--fs-body)] transition-colors",
                  active
                    ? "bg-[var(--bg-hover)]/60 text-foreground"
                    : "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]/40",
                )}
              >
                {active ? (
                  <span
                    aria-hidden
                    className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-[var(--c-accent)]"
                  />
                ) : null}
                {editing ? (
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => onRenameKeyDown(e, t.id)}
                    onBlur={() => setEditingId(null)}
                    onFocus={(e) => e.currentTarget.select()}
                    aria-label="Rename chat"
                    className="min-w-0 flex-1 rounded-sm border border-[var(--border-strong)] bg-transparent px-2 py-1 text-[length:var(--fs-body)] text-foreground outline-none"
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => onSelectThread(t.id)}
                    onDoubleClick={() => beginRename(t)}
                    title={t.title}
                    className="flex min-w-0 flex-1 items-baseline gap-2 px-3 py-1.5 text-left"
                  >
                    <span className="min-w-0 flex-1 truncate">{t.title}</span>
                    <span className="shrink-0 text-[length:var(--fs-micro)] text-[var(--fg-faint)] tabular-nums">
                      {relTime(t.lastMessageAt)}
                    </span>
                  </button>
                )}

                {editing ? null : confirming ? (
                  <span className="flex shrink-0 items-center gap-0.5">
                    <button
                      type="button"
                      onClick={() => void confirmDelete(t.id)}
                      aria-label="Confirm delete"
                      className="rounded-sm p-0.5 text-[var(--c-red)] transition-colors hover:bg-[var(--bg-hover)]"
                    >
                      <Check className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmingId(null)}
                      aria-label="Cancel delete"
                      className="rounded-sm p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
                    >
                      <X className="size-3.5" />
                    </button>
                  </span>
                ) : (
                  <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
                    <button
                      type="button"
                      onClick={() => beginRename(t)}
                      aria-label="Rename chat"
                      className="rounded-sm p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
                    >
                      <Pencil className="size-3" />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setEditingId(null);
                        setConfirmingId(t.id);
                      }}
                      aria-label="Delete chat"
                      className="rounded-sm p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
