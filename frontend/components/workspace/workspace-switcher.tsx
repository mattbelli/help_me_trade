"use client";

import { useEffect, useRef, useState } from "react";
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  CopyIcon,
  PencilIcon,
  PlusIcon,
  Star,
  Trash2Icon,
  XIcon,
} from "lucide-react";

import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import {
  MAX_PAGES_PER_WORKSPACE,
  MAX_WORKSPACES,
} from "@/lib/workspace/layout-persistence";
import { cn } from "@/lib/utils";

/**
 * Workspace manager for the bottom strip. Expands upward into a two-pane
 * panel: workspaces (named GROUPS of pages) on the left, the page library on
 * the right. Pages are linked — the same page in two workspaces is one object,
 * so edits apply everywhere; the library is where pages live independent of
 * any workspace. Which workspace is active is a per-device choice
 * (localStorage); the content itself syncs via the server.
 */

function RowRenameInput({
  initial,
  onCommit,
  onCancel,
  label,
}: {
  initial: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
  label: string;
}) {
  const [draft, setDraft] = useState(initial);
  return (
    <input
      autoFocus
      type="text"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onCommit(draft);
        if (e.key === "Escape") onCancel();
      }}
      onBlur={() => onCommit(draft)}
      className="w-full rounded border border-[var(--c-accent)] bg-[var(--bg-inset)] px-1.5 py-0.5 text-[length:var(--fs-body)] text-foreground outline-none"
      aria-label={label}
    />
  );
}

function PaneLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-1.5 pt-1 pb-1 text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
      {children}
    </div>
  );
}

export function WorkspaceSwitcher() {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const defaultWorkspaceId = useWorkspaceStore((s) => s.defaultWorkspaceId);
  const pageLibrary = useWorkspaceStore((s) => s.pageLibrary);
  const pages = useWorkspaceStore((s) => s.pages);
  const activePageId = useWorkspaceStore((s) => s.activePageId);
  const switchWorkspace = useWorkspaceStore((s) => s.switchWorkspace);
  const addWorkspace = useWorkspaceStore((s) => s.addWorkspace);
  const renameWorkspace = useWorkspaceStore((s) => s.renameWorkspace);
  const deleteWorkspace = useWorkspaceStore((s) => s.deleteWorkspace);
  const setDefaultWorkspace = useWorkspaceStore((s) => s.setDefaultWorkspace);
  const setActivePage = useWorkspaceStore((s) => s.setActivePage);
  const addPage = useWorkspaceStore((s) => s.addPage);
  const addPageFromLibrary = useWorkspaceStore((s) => s.addPageFromLibrary);
  const renamePage = useWorkspaceStore((s) => s.renamePage);
  const removePageFromWorkspace = useWorkspaceStore(
    (s) => s.removePageFromWorkspace,
  );
  const deletePageEverywhere = useWorkspaceStore((s) => s.deletePageEverywhere);
  const movePage = useWorkspaceStore((s) => s.movePage);

  const [open, setOpen] = useState(false);
  const [renamingWsId, setRenamingWsId] = useState<string | null>(null);
  const [renamingPageId, setRenamingPageId] = useState<string | null>(null);
  const [confirmDeleteWsId, setConfirmDeleteWsId] = useState<string | null>(
    null,
  );
  const [confirmDeletePageId, setConfirmDeletePageId] = useState<
    string | null
  >(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (rootRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (open) return;
    setRenamingWsId(null);
    setRenamingPageId(null);
    setConfirmDeleteWsId(null);
    setConfirmDeletePageId(null);
  }, [open]);

  const wsIds = Object.keys(workspaces);
  const active = workspaces[activeWorkspaceId];
  const atWsCap = wsIds.length >= MAX_WORKSPACES;
  const canDeleteWs = wsIds.length > 1;

  const livePageIds = Object.keys(pages);
  const atPageCap = livePageIds.length >= MAX_PAGES_PER_WORKSPACE;
  const canRemovePage = livePageIds.length > 1;
  const libraryOnlyIds = Object.keys(pageLibrary).filter(
    (id) => !pages[id],
  );

  const usageCount = (pageId: string) =>
    Object.values(workspaces).filter((ws) => ws.pageIds.includes(pageId))
      .length;
  // deletePageEverywhere refuses when the page is some workspace's only page.
  const isLastPageSomewhere = (pageId: string) =>
    Object.values(workspaces).some(
      (ws) => ws.pageIds.length === 1 && ws.pageIds[0] === pageId,
    );

  const rowClass =
    "group/row relative flex cursor-default items-center gap-2 rounded-md px-1.5 py-1 hover:bg-[var(--bg-hover)]";
  const iconBtn =
    "rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground";
  const hoverReveal =
    "flex items-center gap-0.5 opacity-0 transition-opacity group-hover/row:opacity-100";

  return (
    <div ref={rootRef} className="relative hidden md:block">
      <button
        type="button"
        title="Workspaces — saved groups of pages"
        onClick={() => setOpen((v) => !v)}
        className="flex max-w-[9rem] items-center gap-1 truncate font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)] transition-colors hover:text-foreground"
        aria-expanded={open}
      >
        <span className="truncate lowercase">{active?.name ?? "workspace"}</span>
        {open ? (
          <ChevronDownIcon className="size-3 shrink-0 text-[var(--fg-faint)]" />
        ) : (
          <ChevronUpIcon className="size-3 shrink-0 text-[var(--fg-faint)]" />
        )}
      </button>

      {open ? (
        <div className="absolute right-0 bottom-[calc(100%+10px)] z-[60] flex max-h-[60vh] w-[560px] max-w-[92vw] overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]">
          {/* Left pane — workspaces */}
          <div className="flex w-1/2 flex-col border-r border-[var(--border)] p-1">
            <PaneLabel>Workspaces</PaneLabel>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {wsIds.map((id) => {
                const ws = workspaces[id];
                const isActive = id === activeWorkspaceId;
                const isDefault = id === defaultWorkspaceId;
                if (renamingWsId === id) {
                  return (
                    <div key={id} className="px-1.5 py-1">
                      <RowRenameInput
                        initial={ws.name}
                        label="Rename workspace"
                        onCommit={(next) => {
                          renameWorkspace(id, next);
                          setRenamingWsId(null);
                        }}
                        onCancel={() => setRenamingWsId(null)}
                      />
                    </div>
                  );
                }
                return (
                  <div key={id} className={rowClass}>
                    <button
                      type="button"
                      onClick={() => switchWorkspace(id)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      <span className="truncate">{ws.name}</span>
                      {isActive ? (
                        <CheckIcon className="size-3.5 shrink-0 text-accent" />
                      ) : null}
                      <span className="ml-auto shrink-0 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                        {ws.pageIds.length} pg
                      </span>
                    </button>
                    <div className="flex shrink-0 items-center gap-0.5">
                      {isDefault ? (
                        <span
                          title="Default workspace (fresh devices open this)"
                          className="p-0.5 text-[var(--c-amber)]"
                        >
                          <Star className="size-3.5 fill-current" />
                        </span>
                      ) : (
                        <button
                          type="button"
                          aria-label={`Set ${ws.name} as default workspace`}
                          title="Set as default"
                          onClick={() => setDefaultWorkspace(id)}
                          className="rounded p-0.5 text-[var(--fg-faint)] opacity-0 transition-opacity group-hover/row:opacity-100 hover:text-[var(--c-amber)]"
                        >
                          <Star className="size-3.5" />
                        </button>
                      )}
                      {confirmDeleteWsId === id ? (
                        <>
                          <button
                            type="button"
                            aria-label="Confirm delete workspace"
                            onClick={() => {
                              deleteWorkspace(id);
                              setConfirmDeleteWsId(null);
                            }}
                            className="rounded p-0.5 text-[var(--c-red)] hover:bg-[var(--c-red)]/15"
                          >
                            <CheckIcon className="size-3.5" />
                          </button>
                          <button
                            type="button"
                            aria-label="Cancel delete"
                            onClick={() => setConfirmDeleteWsId(null)}
                            className={iconBtn}
                          >
                            <XIcon className="size-3.5" />
                          </button>
                        </>
                      ) : (
                        <div className={hoverReveal}>
                          <button
                            type="button"
                            aria-label={`Rename ${ws.name}`}
                            onClick={() => setRenamingWsId(id)}
                            className={iconBtn}
                          >
                            <PencilIcon className="size-3.5" />
                          </button>
                          {canDeleteWs ? (
                            <button
                              type="button"
                              aria-label={`Delete ${ws.name}`}
                              title="Delete workspace (its pages stay in the library)"
                              onClick={() => setConfirmDeleteWsId(id)}
                              className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--c-red)]/15 hover:text-[var(--c-red)]"
                            >
                              <XIcon className="size-3.5" />
                            </button>
                          ) : null}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-1 border-t border-[var(--border)] pt-1">
              <button
                type="button"
                disabled={atWsCap}
                onClick={() => addWorkspace("")}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
                  atWsCap && "cursor-default opacity-50",
                )}
              >
                <PlusIcon className="size-3.5" />
                New workspace
              </button>
              <button
                type="button"
                disabled={atWsCap}
                onClick={() =>
                  addWorkspace(`${active?.name ?? "Workspace"} copy`, true)
                }
                title="Copies this workspace's pages into new, unlinked pages"
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
                  atWsCap && "cursor-default opacity-50",
                )}
              >
                <CopyIcon className="size-3.5" />
                Duplicate current
              </button>
            </div>
          </div>

          {/* Right pane — pages of the active workspace + the library */}
          <div className="flex w-1/2 flex-col p-1">
            <div className="min-h-0 flex-1 overflow-y-auto">
              <PaneLabel>
                Pages in{" "}
                <span className="text-[var(--fg-dim)] normal-case">
                  {active?.name ?? "workspace"}
                </span>
              </PaneLabel>
              {livePageIds.map((id, idx) => {
                const page = pages[id];
                const shared = usageCount(id) > 1;
                if (renamingPageId === id) {
                  return (
                    <div key={id} className="px-1.5 py-1">
                      <RowRenameInput
                        initial={page.name}
                        label="Rename page"
                        onCommit={(next) => {
                          renamePage(id, next);
                          setRenamingPageId(null);
                        }}
                        onCancel={() => setRenamingPageId(null)}
                      />
                    </div>
                  );
                }
                return (
                  <div key={id} className={rowClass}>
                    <button
                      type="button"
                      onClick={() => setActivePage(id)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      <span className="truncate">{page.name}</span>
                      {id === activePageId ? (
                        <CheckIcon className="size-3.5 shrink-0 text-accent" />
                      ) : null}
                      {shared ? (
                        <span
                          title={`In ${usageCount(id)} workspaces — edits apply everywhere`}
                          className="shrink-0 text-[length:var(--fs-micro)] text-[var(--c-amber)]"
                        >
                          shared
                        </span>
                      ) : null}
                    </button>
                    <div className={cn(hoverReveal, "shrink-0")}>
                      <button
                        type="button"
                        aria-label={`Move ${page.name} up`}
                        disabled={idx === 0}
                        onClick={() => movePage(id, -1)}
                        className={cn(iconBtn, idx === 0 && "opacity-30")}
                      >
                        <ChevronUpIcon className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Move ${page.name} down`}
                        disabled={idx === livePageIds.length - 1}
                        onClick={() => movePage(id, 1)}
                        className={cn(
                          iconBtn,
                          idx === livePageIds.length - 1 && "opacity-30",
                        )}
                      >
                        <ChevronDownIcon className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Rename ${page.name}`}
                        onClick={() => setRenamingPageId(id)}
                        className={iconBtn}
                      >
                        <PencilIcon className="size-3.5" />
                      </button>
                      {canRemovePage ? (
                        <button
                          type="button"
                          aria-label={`Remove ${page.name} from this workspace`}
                          title="Remove from this workspace (stays in the library)"
                          onClick={() => removePageFromWorkspace(id)}
                          className={iconBtn}
                        >
                          <XIcon className="size-3.5" />
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
              <button
                type="button"
                disabled={atPageCap}
                onClick={() => addPage()}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
                  atPageCap && "cursor-default opacity-50",
                )}
              >
                <PlusIcon className="size-3.5" />
                New page
              </button>

              <div className="mt-1 border-t border-[var(--border)] pt-1">
                <PaneLabel>Library</PaneLabel>
                {libraryOnlyIds.length === 0 ? (
                  <p className="px-1.5 py-1 text-[var(--fg-faint)]">
                    Every saved page is in this workspace.
                  </p>
                ) : (
                  libraryOnlyIds.map((id) => {
                    const page = pageLibrary[id];
                    const uses = usageCount(id);
                    if (renamingPageId === id) {
                      return (
                        <div key={id} className="px-1.5 py-1">
                          <RowRenameInput
                            initial={page.name}
                            label="Rename page"
                            onCommit={(next) => {
                              renamePage(id, next);
                              setRenamingPageId(null);
                            }}
                            onCancel={() => setRenamingPageId(null)}
                          />
                        </div>
                      );
                    }
                    return (
                      <div key={id} className={rowClass}>
                        <button
                          type="button"
                          disabled={atPageCap}
                          title={
                            atPageCap
                              ? `Workspace is at ${MAX_PAGES_PER_WORKSPACE} pages`
                              : "Add to this workspace (linked — edits apply everywhere it's used)"
                          }
                          onClick={() => addPageFromLibrary(id)}
                          className={cn(
                            "flex min-w-0 flex-1 items-center gap-2 text-left",
                            atPageCap && "cursor-default opacity-50",
                          )}
                        >
                          <PlusIcon className="size-3 shrink-0 text-[var(--fg-faint)]" />
                          <span className="truncate">{page.name}</span>
                          {uses > 0 ? (
                            <span className="shrink-0 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                              in {uses}
                            </span>
                          ) : null}
                        </button>
                        <div className={cn(hoverReveal, "shrink-0")}>
                          <button
                            type="button"
                            aria-label={`Rename ${page.name}`}
                            onClick={() => setRenamingPageId(id)}
                            className={iconBtn}
                          >
                            <PencilIcon className="size-3.5" />
                          </button>
                          {confirmDeletePageId === id ? (
                            <>
                              <button
                                type="button"
                                aria-label="Confirm delete page everywhere"
                                onClick={() => {
                                  deletePageEverywhere(id);
                                  setConfirmDeletePageId(null);
                                }}
                                className="rounded p-0.5 text-[var(--c-red)] hover:bg-[var(--c-red)]/15"
                              >
                                <CheckIcon className="size-3.5" />
                              </button>
                              <button
                                type="button"
                                aria-label="Cancel delete"
                                onClick={() => setConfirmDeletePageId(null)}
                                className={iconBtn}
                              >
                                <XIcon className="size-3.5" />
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              aria-label={`Delete ${page.name} everywhere`}
                              disabled={isLastPageSomewhere(id)}
                              title={
                                isLastPageSomewhere(id)
                                  ? "Some workspace's only page — remove it there first"
                                  : uses > 0
                                    ? `Delete from the library and ${uses} workspace(s)`
                                    : "Delete from the library"
                              }
                              onClick={() => setConfirmDeletePageId(id)}
                              className={cn(
                                "rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--c-red)]/15 hover:text-[var(--c-red)]",
                                isLastPageSomewhere(id) &&
                                  "cursor-default opacity-30 hover:bg-transparent hover:text-[var(--fg-dim)]",
                              )}
                            >
                              <Trash2Icon className="size-3.5" />
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
