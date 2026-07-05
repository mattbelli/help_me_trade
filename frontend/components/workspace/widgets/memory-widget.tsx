"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  deleteMemory,
  listMemories,
  MEMORY_TYPE_LABELS,
  type MemoryRecord,
  type MemoryType,
} from "@/lib/memories";
import { relativeTime } from "@/lib/relative-time";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 20_000;

type TypeFilter = "all" | MemoryType;

const TYPE_FILTERS: { id: TypeFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "preference", label: "Pref" },
  { id: "lesson", label: "Lesson" },
  { id: "research", label: "Research" },
];

function typeChipClass(type: MemoryType): string {
  switch (type) {
    case "preference":
      return "text-[var(--c-accent)] border-[color-mix(in_srgb,var(--c-accent)_35%,transparent)]";
    case "lesson":
      return "text-[var(--c-amber)] border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)]";
    case "research":
      return "text-[var(--c-blue)] border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)]";
  }
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await listMemories(
          typeFilter === "all" ? undefined : { type: typeFilter },
        );
        if (cancelled) return;
        setMemories(sortNewestFirst(data));
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
  }, [typeFilter]);

  const handleDelete = useCallback(async (memory: MemoryRecord) => {
    setMemories((prev) => prev.filter((item) => item.id !== memory.id));
    try {
      await deleteMemory(memory.id);
    } catch (err) {
      setMemories((prev) => sortNewestFirst([...prev, memory]));
      setError(err instanceof Error ? err.message : "Failed to delete memory");
    }
  }, []);

  const visibleMemories = useMemo(
    () => sortNewestFirst(memories),
    [memories],
  );

  if (loading && memories.length === 0) {
    return <WidgetLoading cols={[3, 1]} />;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden px-1.5 py-1">
      <div className="mb-1.5 flex shrink-0 flex-wrap gap-1 px-1">
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

      {error && visibleMemories.length === 0 ? (
        <WidgetError message={error} className="p-2" />
      ) : null}

      {visibleMemories.length === 0 && !error ? (
        <WidgetEmpty
          message="No memories yet — the copilot learns preferences and lessons over time."
          className="p-2"
        />
      ) : visibleMemories.length === 0 ? null : (
        <div className="min-h-0 flex-1 overflow-auto">
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

              return (
              <li
                key={memory.id}
                className="group flex gap-2 rounded px-1 py-1.5 transition-colors hover:bg-[var(--bg-hover)]"
              >
                {openResearch ? (
                  <button
                    type="button"
                    onClick={openResearch}
                    className="min-w-0 flex-1 cursor-pointer space-y-1 text-left"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={cn(
                          "rounded border px-1 py-px font-mono text-[length:var(--fs-micro)] uppercase tracking-[0.08em]",
                          typeChipClass(memory.type),
                        )}
                      >
                        {MEMORY_TYPE_LABELS[memory.type]}
                      </span>
                      <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
                        {relativeTime(memory.created_at)}
                      </span>
                    </div>
                    <p className="line-clamp-3 font-mono text-[length:var(--fs-body)] leading-snug text-foreground">
                      {memory.content}
                    </p>
                  </button>
                ) : (
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
                    <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
                      {relativeTime(memory.created_at)}
                    </span>
                  </div>
                  <p className="line-clamp-3 font-mono text-[length:var(--fs-body)] leading-snug text-foreground">
                    {memory.content}
                  </p>
                </div>
                )}
                <button
                  type="button"
                  className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] opacity-0 transition-all group-hover:opacity-100 hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                  aria-label="Delete memory"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDelete(memory);
                  }}
                >
                  ✕
                </button>
              </li>
            );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
