"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Plus } from "lucide-react";

import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";

const MAX_PAGES = 5;

type ContextMenuState = {
  pageId: string;
  x: number;
  y: number;
};

function PageDeleteConfirm({
  pageName,
  onCancel,
  onConfirm,
}: {
  pageName: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="w-44 border-[var(--border)] bg-[var(--panel)] p-2 font-mono shadow-[var(--panel-shadow)]">
      <p className="px-1 py-1 text-[length:var(--fs-body)] text-[var(--fg-dim)]">
        Delete &ldquo;{pageName}&rdquo; and its widgets?
      </p>
      <div className="mt-1.5 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-[var(--border)] px-2 py-0.5 text-[length:var(--fs-table)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="rounded border border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] px-2 py-0.5 text-[length:var(--fs-table)] text-[var(--c-red)] transition-colors hover:bg-[color-mix(in_srgb,var(--c-red)_12%,transparent)]"
        >
          Delete
        </button>
      </div>
    </div>
  );
}

function PagePill({
  name,
  active,
  renaming,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onContextMenu,
  onSelect,
}: {
  name: string;
  active: boolean;
  renaming: boolean;
  onStartRename: () => void;
  onCommitRename: (next: string) => void;
  onCancelRename: () => void;
  onContextMenu: (event: MouseEvent<HTMLButtonElement>) => void;
  onSelect: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(name);

  useEffect(() => {
    if (!renaming) return;
    setDraft(name);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }, [name, renaming]);

  const commit = useCallback(() => {
    onCommitRename(draft);
  }, [draft, onCommitRename]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Enter") {
        event.preventDefault();
        commit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        onCancelRename();
      }
    },
    [commit, onCancelRename],
  );

  if (renaming) {
    return (
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={commit}
        onClick={(e) => e.stopPropagation()}
        className="max-w-[7rem] rounded-full border border-[var(--c-accent)] bg-[var(--bg-inset)] px-2 py-0.5 font-mono text-[length:var(--fs-table)] text-foreground outline-none"
        aria-label="Rename page"
      />
    );
  }

  return (
    <button
      type="button"
      onClick={onSelect}
      onContextMenu={onContextMenu}
      onDoubleClick={(e) => {
        e.preventDefault();
        onStartRename();
      }}
      className={cn(
        "max-w-[7rem] truncate rounded-full border px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
        active
          ? "border-[var(--c-accent)] bg-[var(--c-accent-soft)] text-foreground"
          : "border-[var(--border)] text-[var(--fg-dim)] hover:border-[var(--border-strong)] hover:text-foreground",
      )}
      title={`${name} — double-click to rename`}
      aria-current={active ? "page" : undefined}
    >
      {name}
    </button>
  );
}

export function PageSwitcher() {
  const pages = useWorkspaceStore((s) => s.pages);
  const activePageId = useWorkspaceStore((s) => s.activePageId);
  const setActivePage = useWorkspaceStore((s) => s.setActivePage);
  const addPage = useWorkspaceStore((s) => s.addPage);
  const renamePage = useWorkspaceStore((s) => s.renamePage);
  const deletePage = useWorkspaceStore((s) => s.deletePage);
  const duplicatePage = useWorkspaceStore((s) => s.duplicatePage);

  const pageIds = Object.keys(pages);
  const atCap = pageIds.length >= MAX_PAGES;
  const canDelete = pageIds.length > 1;

  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [renamingPageId, setRenamingPageId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const contextPage = contextMenu ? pages[contextMenu.pageId] : undefined;

  useEffect(() => {
    if (!contextMenu) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target)) return;
      setContextMenu(null);
      setConfirmDeleteId(null);
    }

    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setContextMenu(null);
        setConfirmDeleteId(null);
      }
    }

    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [contextMenu]);

  const openContextMenu = useCallback(
    (pageId: string, event: MouseEvent<HTMLButtonElement>) => {
      event.preventDefault();
      setConfirmDeleteId(null);
      setContextMenu({ pageId, x: event.clientX, y: event.clientY });
    },
    [],
  );

  const handleDelete = useCallback(
    (pageId: string) => {
      const page = pages[pageId];
      if (!page || !canDelete) return;

      if (page.layout.length > 0) {
        setConfirmDeleteId(pageId);
        return;
      }

      deletePage(pageId);
      setContextMenu(null);
    },
    [canDelete, deletePage, pages],
  );

  return (
    <>
      <div className="hidden items-center gap-1 md:flex">
        {pageIds.map((pageId) => {
          const page = pages[pageId];
          if (!page) return null;
          return (
            <PagePill
              key={pageId}
              name={page.name}
              active={pageId === activePageId}
              renaming={renamingPageId === pageId}
              onSelect={() => setActivePage(pageId)}
              onStartRename={() => {
                setRenamingPageId(pageId);
                setContextMenu(null);
              }}
              onCommitRename={(next) => {
                renamePage(pageId, next);
                setRenamingPageId(null);
              }}
              onCancelRename={() => setRenamingPageId(null)}
              onContextMenu={(event) => openContextMenu(pageId, event)}
            />
          );
        })}

        {!atCap ? (
          <button
            type="button"
            onClick={() => addPage()}
            className="flex items-center gap-0.5 rounded-full border border-[var(--border)] px-1.5 py-0.5 font-mono text-[var(--fg-faint)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)] hover:text-foreground"
            aria-label="Add page"
            title="Add page"
          >
            <Plus className="size-2.5" />
            <span className="text-[length:var(--fs-micro)] uppercase tracking-[0.08em]">pg</span>
          </button>
        ) : null}
      </div>

      {contextMenu && contextPage ? (
        <div
          ref={menuRef}
          className="fixed z-[60] overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-lg"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          {confirmDeleteId === contextMenu.pageId ? (
            <PageDeleteConfirm
              pageName={contextPage.name}
              onCancel={() => setConfirmDeleteId(null)}
              onConfirm={() => {
                deletePage(contextMenu.pageId);
                setContextMenu(null);
                setConfirmDeleteId(null);
              }}
            />
          ) : (
            <div className="min-w-28 p-1 font-mono">
              <button
                type="button"
                className="flex w-full rounded-md px-2 py-1 text-left text-[length:var(--fs-body)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
                onClick={() => {
                  setRenamingPageId(contextMenu.pageId);
                  setContextMenu(null);
                }}
              >
                Rename
              </button>
              {!atCap ? (
                <button
                  type="button"
                  className="flex w-full rounded-md px-2 py-1 text-left text-[length:var(--fs-body)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
                  onClick={() => {
                    duplicatePage(contextMenu.pageId);
                    setContextMenu(null);
                  }}
                >
                  Duplicate
                </button>
              ) : null}
              {canDelete ? (
                <button
                  type="button"
                  className="flex w-full rounded-md px-2 py-1 text-left text-[length:var(--fs-body)] text-[var(--c-red)] transition-colors hover:bg-[color-mix(in_srgb,var(--c-red)_12%,transparent)]"
                  onClick={() => handleDelete(contextMenu.pageId)}
                >
                  Delete
                </button>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </>
  );
}
