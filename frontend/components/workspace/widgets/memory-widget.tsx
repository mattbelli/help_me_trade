"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  archiveMemory,
  confirmMemory,
  deleteMemory,
  listMemories,
  MEMORY_TYPE_LABELS,
  restoreMemory,
  setMemoryPinned,
  type MemoryRecord,
  type MemorySource,
  type MemoryType,
} from "@/lib/memories";
import { relativeTime } from "@/lib/relative-time";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 20_000;

type TypeFilter = "all" | MemoryType;
type StatusView = "active" | "history";

const TYPE_FILTERS: { id: TypeFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "directive", label: "Dir" },
  { id: "preference", label: "Pref" },
  { id: "fact", label: "Fact" },
  { id: "lesson", label: "Lesson" },
  { id: "thesis", label: "Thesis" },
];

function typeChipClass(type: MemoryType): string {
  switch (type) {
    case "directive":
      return "text-[var(--c-red)] border-[color-mix(in_srgb,var(--c-red)_35%,transparent)]";
    case "preference":
      return "text-[var(--c-accent)] border-[color-mix(in_srgb,var(--c-accent)_35%,transparent)]";
    case "fact":
      return "text-[var(--c-green)] border-[color-mix(in_srgb,var(--c-green)_35%,transparent)]";
    case "lesson":
      return "text-[var(--c-amber)] border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)]";
    case "thesis":
      return "text-[var(--c-purple)] border-[color-mix(in_srgb,var(--c-purple)_35%,transparent)]";
    case "research":
      return "text-[var(--c-blue)] border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)]";
  }
}

const SOURCE_LABELS: Record<string, string> = {
  user_explicit: "you said",
  widget_manual: "you",
  chat_extract: "extracted",
  reflection: "reflection",
  retrospective: "retrospective",
  research_promote: "research",
  agent_save: "agent",
  legacy: "legacy",
};

function sourceLabel(source?: MemorySource): string {
  if (!source) return "—";
  return SOURCE_LABELS[source] ?? source;
}

function sessionHref(memory: MemoryRecord): string | null {
  const sid = memory.metadata?.session_id;
  return typeof sid === "string" && sid ? `/chat?session=${sid}` : null;
}

function sortNewestFirst(items: MemoryRecord[]): MemoryRecord[] {
  return [...items].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  );
}

function researchArtifactId(memory: MemoryRecord): number | null {
  if (memory.type !== "research") return null;
  const raw = memory.metadata?.artifact_id;
  const id = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function MemoryWidget({}: WidgetProps) {
  const router = useRouter();
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [statusView, setStatusView] = useState<StatusView>("active");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await listMemories({
          type: typeFilter === "all" ? undefined : typeFilter,
          includeInactive: statusView === "history",
        });
        if (cancelled) return;
        const scoped =
          statusView === "history"
            ? data.filter((m) => (m.status ?? "active") !== "active")
            : data;
        setMemories(sortNewestFirst(scoped));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load memories");
        setMemories([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    const timer = setInterval(load, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [typeFilter, statusView]);

  const patchLocal = useCallback(
    (id: number, next: Partial<MemoryRecord>) => {
      setMemories((prev) =>
        prev.map((m) => (m.id === id ? { ...m, ...next } : m)),
      );
    },
    [],
  );

  const removeLocal = useCallback((id: number) => {
    setMemories((prev) => prev.filter((m) => m.id !== id));
  }, []);

  const handleDelete = useCallback(
    async (memory: MemoryRecord) => {
      removeLocal(memory.id);
      try {
        await deleteMemory(memory.id);
      } catch (err) {
        setMemories((prev) => sortNewestFirst([...prev, memory]));
        setError(err instanceof Error ? err.message : "Failed to delete memory");
      }
    },
    [removeLocal],
  );

  const handleArchive = useCallback(
    async (memory: MemoryRecord) => {
      removeLocal(memory.id);
      try {
        await archiveMemory(memory.id);
      } catch (err) {
        setMemories((prev) => sortNewestFirst([...prev, memory]));
        setError(err instanceof Error ? err.message : "Failed to archive memory");
      }
    },
    [removeLocal],
  );

  const handleRestore = useCallback(
    async (memory: MemoryRecord) => {
      removeLocal(memory.id);
      try {
        await restoreMemory(memory.id);
      } catch (err) {
        setMemories((prev) => sortNewestFirst([...prev, memory]));
        setError(err instanceof Error ? err.message : "Failed to restore memory");
      }
    },
    [removeLocal],
  );

  const handlePin = useCallback(
    async (memory: MemoryRecord) => {
      const next = !memory.pinned;
      patchLocal(memory.id, { pinned: next });
      try {
        await setMemoryPinned(memory.id, next);
      } catch (err) {
        patchLocal(memory.id, { pinned: memory.pinned });
        setError(err instanceof Error ? err.message : "Failed to pin memory");
      }
    },
    [patchLocal],
  );

  const handleConfirm = useCallback(
    async (memory: MemoryRecord) => {
      patchLocal(memory.id, { authority: "user_confirmed" });
      try {
        await confirmMemory(memory.id);
      } catch (err) {
        patchLocal(memory.id, { authority: memory.authority });
        setError(err instanceof Error ? err.message : "Failed to confirm memory");
      }
    },
    [patchLocal],
  );

  const toggleExpanded = useCallback((id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const visibleMemories = useMemo(() => sortNewestFirst(memories), [memories]);
  const history = statusView === "history";

  if (loading && memories.length === 0) {
    return <WidgetLoading cols={[3, 1]} />;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden px-1.5 py-1">
      <div className="mb-1.5 flex shrink-0 items-center justify-between gap-1 px-1">
        <div className="flex flex-wrap gap-1">
          {TYPE_FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              className={cn(
                "rounded border px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
                typeFilter === filter.id
                  ? "border-[var(--border)] bg-[var(--bg-hover)] text-foreground"
                  : "border-transparent text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
              )}
              onClick={() => setTypeFilter(filter.id)}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <div className="flex shrink-0 gap-1">
          {(["active", "history"] as StatusView[]).map((view) => (
            <button
              key={view}
              type="button"
              className={cn(
                "rounded border px-1.5 py-0.5 font-mono text-[length:var(--fs-micro)] uppercase tracking-[0.08em] transition-colors",
                statusView === view
                  ? "border-[var(--border)] bg-[var(--bg-hover)] text-foreground"
                  : "border-transparent text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
              )}
              onClick={() => setStatusView(view)}
            >
              {view === "active" ? "Active" : "History"}
            </button>
          ))}
        </div>
      </div>

      {error && visibleMemories.length === 0 ? (
        <WidgetError message={error} className="p-2" />
      ) : null}

      {visibleMemories.length === 0 && !error ? (
        <WidgetEmpty
          message={
            history
              ? "No superseded or archived memories."
              : "No memories yet — the copilot learns preferences and lessons over time."
          }
          className="p-2"
        />
      ) : visibleMemories.length === 0 ? null : (
        <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
          {error ? (
            <p className="mb-1 px-1 font-mono text-[length:var(--fs-table)] text-[var(--c-red)]">
              {error}
            </p>
          ) : null}
          <ul className="space-y-1">
            {visibleMemories.map((memory) => {
              const artifactId = researchArtifactId(memory);
              const openResearch = artifactId
                ? () => router.push(`/research?id=${artifactId}`)
                : undefined;
              const isExpanded = expanded.has(memory.id);
              const href = sessionHref(memory);
              const unconfirmed =
                (memory.authority ?? "agent_inferred") === "agent_inferred";
              const inactive = (memory.status ?? "active") !== "active";

              return (
                <li
                  key={memory.id}
                  className={cn(
                    "group flex gap-2 rounded px-1 py-1.5 transition-colors hover:bg-[var(--bg-hover)]",
                    inactive && "opacity-70",
                  )}
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={cn(
                          "rounded border px-1 py-px font-mono text-[length:var(--fs-micro)] uppercase tracking-[0.08em]",
                          typeChipClass(memory.type),
                        )}
                      >
                        {MEMORY_TYPE_LABELS[memory.type]}
                      </span>
                      {memory.pinned ? (
                        <span
                          className="font-mono text-[length:var(--fs-micro)] text-[var(--c-amber)]"
                          title="Pinned — always applied"
                        >
                          ★
                        </span>
                      ) : null}
                      {history ? (
                        <span className="font-mono text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
                          {memory.status}
                          {memory.supersedes_id
                            ? ` → [mem:${memory.supersedes_id}]`
                            : ""}
                        </span>
                      ) : null}
                    </div>

                    {openResearch ? (
                      <button
                        type="button"
                        onClick={openResearch}
                        className="block w-full cursor-pointer text-left"
                      >
                        <p className="line-clamp-3 font-mono text-[length:var(--fs-body)] leading-snug text-foreground">
                          {memory.content}
                        </p>
                      </button>
                    ) : (
                      <p className="line-clamp-3 font-mono text-[length:var(--fs-body)] leading-snug text-foreground">
                        {memory.content}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-1.5 font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                      {href ? (
                        <button
                          type="button"
                          className="hover:text-[var(--fg-dim)] hover:underline"
                          onClick={() => router.push(href)}
                        >
                          {sourceLabel(memory.source)}
                        </button>
                      ) : (
                        <span>{sourceLabel(memory.source)}</span>
                      )}
                      <span aria-hidden>·</span>
                      <span className="tabular-nums">
                        {relativeTime(memory.created_at)}
                      </span>
                      {unconfirmed && !inactive ? (
                        <span className="text-[var(--c-amber)]">· unconfirmed</span>
                      ) : null}
                      {memory.quote ? (
                        <button
                          type="button"
                          className="hover:text-[var(--fg-dim)] hover:underline"
                          onClick={() => toggleExpanded(memory.id)}
                        >
                          · {isExpanded ? "hide quote" : "quote"}
                        </button>
                      ) : null}
                    </div>

                    {isExpanded && memory.quote ? (
                      <p className="border-l-2 border-[var(--border)] pl-2 font-mono text-[length:var(--fs-micro)] italic leading-snug text-[var(--fg-dim)]">
                        saved from: “{memory.quote}”
                      </p>
                    ) : null}
                  </div>

                  <div className="mt-0.5 flex shrink-0 flex-col items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    {inactive ? (
                      <button
                        type="button"
                        className="inline-flex size-5 items-center justify-center rounded font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)] hover:bg-[var(--bg-hover)] hover:text-[var(--c-green)]"
                        aria-label="Restore memory"
                        title="Restore to active"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleRestore(memory);
                        }}
                      >
                        ↩
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          className={cn(
                            "inline-flex size-5 items-center justify-center rounded font-mono text-[length:var(--fs-micro)] hover:bg-[var(--bg-hover)]",
                            memory.pinned
                              ? "text-[var(--c-amber)]"
                              : "text-[var(--fg-faint)] hover:text-[var(--c-amber)]",
                          )}
                          aria-label={memory.pinned ? "Unpin" : "Pin"}
                          title={memory.pinned ? "Unpin" : "Pin (always apply)"}
                          onClick={(e) => {
                            e.stopPropagation();
                            void handlePin(memory);
                          }}
                        >
                          {memory.pinned ? "★" : "☆"}
                        </button>
                        {unconfirmed ? (
                          <button
                            type="button"
                            className="inline-flex size-5 items-center justify-center rounded font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)] hover:bg-[var(--bg-hover)] hover:text-[var(--c-green)]"
                            aria-label="Confirm memory"
                            title="Confirm — treat as your own rule"
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleConfirm(memory);
                            }}
                          >
                            ✓
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="inline-flex size-5 items-center justify-center rounded font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)] hover:bg-[var(--bg-hover)] hover:text-[var(--c-amber)]"
                          aria-label="Archive memory"
                          title="Archive (restorable from History)"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleArchive(memory);
                          }}
                        >
                          ⌫
                        </button>
                        <button
                          type="button"
                          className="inline-flex size-5 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                          aria-label="Delete memory"
                          title="Delete permanently"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleDelete(memory);
                          }}
                        >
                          ✕
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
