"use client";

import { useState } from "react";
import {
  CheckIcon,
  ChevronUpIcon,
  CopyIcon,
  PencilIcon,
  PlusIcon,
  Star,
  XIcon,
} from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { MAX_WORKSPACES } from "@/lib/workspace/layout-persistence";
import { cn } from "@/lib/utils";

/**
 * Workspace picker for the bottom strip: a named GROUP of pages per cockpit
 * arrangement (widescreen vs laptop). Which workspace is active is a
 * per-device choice (localStorage); the workspaces themselves sync via the
 * server like pages do.
 */
export function WorkspaceSwitcher() {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const defaultWorkspaceId = useWorkspaceStore((s) => s.defaultWorkspaceId);
  const switchWorkspace = useWorkspaceStore((s) => s.switchWorkspace);
  const addWorkspace = useWorkspaceStore((s) => s.addWorkspace);
  const renameWorkspace = useWorkspaceStore((s) => s.renameWorkspace);
  const deleteWorkspace = useWorkspaceStore((s) => s.deleteWorkspace);
  const setDefaultWorkspace = useWorkspaceStore((s) => s.setDefaultWorkspace);

  const [open, setOpen] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const ids = Object.keys(workspaces);
  const active = workspaces[activeWorkspaceId];
  const atCap = ids.length >= MAX_WORKSPACES;
  const canDelete = ids.length > 1;

  function commitRename(id: string) {
    renameWorkspace(id, draft);
    setRenamingId(null);
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        render={
          <button
            type="button"
            title="Workspace — a saved group of pages"
            className="hidden max-w-[9rem] items-center gap-1 truncate font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)] transition-colors hover:text-foreground md:flex"
          />
        }
      >
        <span className="truncate lowercase">{active?.name ?? "workspace"}</span>
        <ChevronUpIcon className="size-3 shrink-0 text-[var(--fg-faint)]" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="top"
        className="min-w-52 font-mono text-[length:var(--fs-body)]"
      >
        <div className="px-1.5 pt-1 pb-1 text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
          Workspaces
        </div>
        {ids.map((id) => {
          const ws = workspaces[id];
          const isActive = id === activeWorkspaceId;
          const isDefault = id === defaultWorkspaceId;
          if (renamingId === id) {
            return (
              <div key={id} className="px-1.5 py-1">
                <input
                  autoFocus
                  type="text"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(id);
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                  onBlur={() => commitRename(id)}
                  className="w-full rounded border border-[var(--c-accent)] bg-[var(--bg-inset)] px-1.5 py-0.5 text-foreground outline-none"
                  aria-label="Rename workspace"
                />
              </div>
            );
          }
          return (
            <div
              key={id}
              className="group/ws relative flex cursor-default items-center gap-2 rounded-md px-1.5 py-1 hover:bg-accent hover:text-accent-foreground"
            >
              <button
                type="button"
                onClick={() => {
                  switchWorkspace(id);
                  setOpen(false);
                }}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <span className="truncate">{ws.name}</span>
                {isActive ? (
                  <CheckIcon className="size-3.5 shrink-0 text-accent" />
                ) : null}
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
                    className="rounded p-0.5 text-[var(--fg-faint)] opacity-0 transition-opacity group-hover/ws:opacity-100 hover:text-[var(--c-amber)]"
                  >
                    <Star className="size-3.5" />
                  </button>
                )}
                {confirmingDelete === id ? (
                  <>
                    <button
                      type="button"
                      aria-label="Confirm delete workspace"
                      onClick={() => {
                        deleteWorkspace(id);
                        setConfirmingDelete(null);
                      }}
                      className="rounded p-0.5 text-[var(--c-red)] hover:bg-[var(--c-red)]/15"
                    >
                      <CheckIcon className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label="Cancel delete"
                      onClick={() => setConfirmingDelete(null)}
                      className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]"
                    >
                      <XIcon className="size-3.5" />
                    </button>
                  </>
                ) : (
                  <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover/ws:opacity-100">
                    <button
                      type="button"
                      aria-label={`Rename ${ws.name}`}
                      onClick={() => {
                        setDraft(ws.name);
                        setRenamingId(id);
                      }}
                      className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
                    >
                      <PencilIcon className="size-3.5" />
                    </button>
                    {canDelete ? (
                      <button
                        type="button"
                        aria-label={`Delete ${ws.name}`}
                        onClick={() => setConfirmingDelete(id)}
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

        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={atCap}
          onClick={() => addWorkspace("")}
          className="gap-2"
        >
          <PlusIcon className="size-3.5" />
          New workspace
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={atCap}
          onClick={() => addWorkspace(`${active?.name ?? "Workspace"} copy`, true)}
          className={cn("gap-2", atCap && "opacity-50")}
        >
          <CopyIcon className="size-3.5" />
          Duplicate current
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
