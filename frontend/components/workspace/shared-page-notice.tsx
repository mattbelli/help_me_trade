"use client";

import { useCallback, useEffect, useState } from "react";
import { XIcon } from "lucide-react";

import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";

// Page ids whose "shared" notice the user has dismissed — shown once per page,
// per device, not per edit.
const SEEN_KEY = "hmt-shared-page-notice-seen";

function loadSeen(): string[] {
  try {
    const raw = window.localStorage.getItem(SEEN_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function markSeen(pageId: string): void {
  try {
    const seen = loadSeen();
    if (!seen.includes(pageId)) {
      window.localStorage.setItem(SEEN_KEY, JSON.stringify([...seen, pageId]));
    }
  } catch {
    // ignore quota / private mode
  }
}

/**
 * One-time heads-up when the user lands on a page that lives in 2+ workspaces:
 * pages are linked, so edits here apply everywhere. Offers the escape hatch —
 * detach a private copy for this workspace.
 */
export function SharedPageNotice() {
  const activePageId = useWorkspaceStore((s) => s.activePageId);
  const pages = useWorkspaceStore((s) => s.pages);
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const hydrated = useWorkspaceStore((s) => s.hydrated);
  const forkPage = useWorkspaceStore((s) => s.forkPage);

  const [seen, setSeen] = useState<string[] | null>(null);
  useEffect(() => {
    setSeen(loadSeen());
  }, []);

  const dismiss = useCallback(() => {
    markSeen(activePageId);
    setSeen(loadSeen());
  }, [activePageId]);

  if (!hydrated || seen === null) return null;
  const page = pages[activePageId];
  if (!page || seen.includes(activePageId)) return null;
  const sharedWith = Object.values(workspaces).filter((ws) =>
    ws.pageIds.includes(activePageId),
  ).length;
  if (sharedWith < 2) return null;

  return (
    <div className="pointer-events-auto absolute inset-x-0 bottom-[40px] z-30 flex items-center gap-2 border-t border-[var(--border)] bg-[var(--bg-raised)]/90 px-4 py-1 font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)] backdrop-blur-md">
      <span className="text-[var(--c-amber)]">shared</span>
      <span className="min-w-0 truncate">
        &ldquo;{page.name}&rdquo; is in {sharedWith} workspaces — edits here
        apply to all of them
      </span>
      <button
        type="button"
        onClick={() => {
          forkPage(activePageId);
        }}
        className="shrink-0 rounded border border-[var(--border)] px-1.5 py-px transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)] hover:text-foreground"
      >
        detach a copy here
      </button>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss shared-page notice"
        className="ml-auto shrink-0 rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
      >
        <XIcon className="size-3.5" />
      </button>
    </div>
  );
}
