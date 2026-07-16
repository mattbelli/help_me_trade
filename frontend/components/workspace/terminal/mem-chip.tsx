"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  listMemories,
  updateMemory,
  type MemoryRecord,
  type MemorySource,
} from "@/lib/memories";
import { cn } from "@/lib/utils";

/**
 * T2 `[mem:ID]` chip + provenance popover (contract: MEMORY_OVERHAUL_DESIGN.md
 * §7). Ships renderer-ready but DORMANT: standing memory injection is off until
 * memory-overhaul Phase 2, so no `[mem:ID]` tokens reach the transcript yet and
 * the flag defaults OFF — tokens render as plain text. Flip on with
 * `NEXT_PUBLIC_MEM_CHIPS=1`, `localStorage.hmt-mem-chips=1`, or `?memchips=1`.
 */
export function memChipsEnabled(): boolean {
  if (typeof window === "undefined") {
    return process.env.NEXT_PUBLIC_MEM_CHIPS === "1";
  }
  try {
    if (localStorage.getItem("hmt-mem-chips") === "1") return true;
    if (new URLSearchParams(window.location.search).get("memchips") === "1") {
      return true;
    }
  } catch {
    // ignore
  }
  return process.env.NEXT_PUBLIC_MEM_CHIPS === "1";
}

export const MEM_TOKEN = /\[mem:(\d+)\]/g;

const SOURCE_LABELS: Record<string, string> = {
  user_explicit: "you said",
  chat_extract: "extracted",
  reflection: "reflection",
  research_promote: "research",
  retrospective: "retrospective",
};

function sourceLabel(source: MemorySource | undefined): string {
  if (!source) return "memory";
  return SOURCE_LABELS[source] ?? source;
}

function ageLabel(iso: string | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return months < 12 ? `${months}mo ago` : `${Math.floor(months / 12)}y ago`;
}

export function MemChip({ id }: { id: number }) {
  const [open, setOpen] = useState(false);
  const [record, setRecord] = useState<MemoryRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);

  // Lazy-load provenance the first time the popover opens (not on mount).
  const loadRecord = useCallback(() => {
    if (record || loading) return;
    setLoading(true);
    listMemories({ includeInactive: true })
      .then((all) => setRecord(all.find((m) => m.id === id) ?? null))
      .catch(() => setRecord(null))
      .finally(() => setLoading(false));
  }, [record, loading, id]);

  useEffect(() => {
    if (!open) return;
    function onDown(e: PointerEvent) {
      if (rootRef.current && e.target instanceof Node && !rootRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const unconfirmed = record?.authority === "agent_inferred";

  const confirm = useCallback(async () => {
    if (!record || confirming) return;
    setConfirming(true);
    try {
      const next = await updateMemory(record.id, { authority: "user_confirmed" });
      setRecord(next);
    } catch {
      // leave as-is; founder can retry
    } finally {
      setConfirming(false);
    }
  }, [record, confirming]);

  return (
    <span ref={rootRef} className="relative inline-block align-baseline">
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          loadRecord();
        }}
        className={cn(
          "rounded-sm border border-[var(--border)] px-1 py-px text-[length:var(--fs-micro)] tabular-nums transition-colors",
          unconfirmed
            ? "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]"
            : "text-[var(--c-accent)] hover:bg-[var(--c-accent-soft)]",
        )}
        aria-label={`Memory ${id} provenance`}
      >
        mem:{id}
        {unconfirmed ? (
          <span className="ml-1 text-[var(--fg-faint)]">(unconfirmed)</span>
        ) : null}
      </button>
      {open ? (
        <span
          role="dialog"
          className="absolute bottom-full left-0 z-50 mb-1 block w-64 rounded-md border border-[var(--border)] bg-[var(--bg-raised)] p-2.5 text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
        >
          {loading ? (
            <span className="text-[var(--fg-faint)]">Loading…</span>
          ) : record ? (
            <span className="flex flex-col gap-1.5">
              <span className="flex items-center gap-1.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                <span className="rounded-sm bg-[var(--bg-hover)] px-1 py-px text-[var(--fg-dim)]">
                  {sourceLabel(record.source)}
                </span>
                <span>{ageLabel(record.created_at)}</span>
                {unconfirmed ? <span>· unconfirmed</span> : null}
              </span>
              <span className="text-[var(--foreground)]">{record.content}</span>
              {record.quote ? (
                <span className="border-l-2 border-[var(--border-strong)] pl-2 text-[var(--fg-dim)] italic">
                  saved from: &ldquo;{record.quote}&rdquo;
                </span>
              ) : null}
              {record.supersedes_id ? (
                <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                  → replaces mem:{record.supersedes_id}
                </span>
              ) : null}
              {unconfirmed ? (
                <button
                  type="button"
                  onClick={() => void confirm()}
                  disabled={confirming}
                  className="mt-0.5 w-fit rounded-sm border border-[var(--border)] px-2 py-0.5 text-[length:var(--fs-micro)] text-[var(--c-accent)] transition-colors hover:bg-[var(--c-accent-soft)] disabled:opacity-50"
                >
                  Confirm as my rule
                </button>
              ) : null}
            </span>
          ) : (
            <span className="text-[var(--fg-faint)]">Memory {id} not found.</span>
          )}
        </span>
      ) : null}
    </span>
  );
}
