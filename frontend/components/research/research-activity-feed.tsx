"use client";

import { useEffect, useMemo, useRef } from "react";

import type { ResearchActivityEvent } from "@/lib/research";
import { cn } from "@/lib/utils";

const VISIBLE_EVENTS = 8;

type LineTone = "default" | "warn" | "error" | "success";

function formatEventLine(event: ResearchActivityEvent): {
  text: string;
  tone: LineTone;
  skip?: boolean;
} {
  switch (event.phase) {
    case "started":
      return { text: "", tone: "default", skip: true };
    case "planning":
      return { text: "▸ planning research angles…", tone: "default" };
    case "specialist_started":
      return {
        text: `▶ ${String(event.title ?? event.key ?? "specialist")}`,
        tone: "default",
      };
    case "specialist_round": {
      const key = String(event.key ?? "specialist");
      const round = Number(event.round ?? 1);
      const queries = Array.isArray(event.queries)
        ? event.queries.map((q) => String(q))
        : [];
      const first = queries[0] ?? "";
      const more = queries.length > 1 ? ` +${queries.length - 1} more` : "";
      const followUp = event.follow_up ? " · follow-up" : "";
      return {
        text: `${key} · round ${round}${followUp} · searching: ${first}${more}`,
        tone: "default",
      };
    }
    case "specialist_done": {
      const key = String(event.key ?? "specialist");
      if (event.status === "failed") {
        return { text: `✗ ${key} failed`, tone: "error" };
      }
      return {
        text: `✓ ${key} — ${Number(event.rounds ?? 0)} rounds, ${Number(event.sources ?? 0)} sources`,
        tone: "success",
      };
    }
    case "critic":
      return {
        text: `⚖ critic reviewing ${Number(event.specialists_ok ?? 0)} sections`,
        tone: "default",
      };
    case "web_warning":
      return {
        text: `⚠ ${String(event.warning ?? "web search warning")}`,
        tone: "warn",
      };
    case "writing":
      return {
        text: `✍ editor writing (target ${Number(event.target_words ?? 0)} words)`,
        tone: "default",
      };
    case "expanding":
      return {
        text: `✍ expanding thin sections (${Number(event.words ?? 0)}/${Number(event.target ?? 0)})`,
        tone: "default",
      };
    case "completed":
      return { text: "✓ done", tone: "success" };
    case "error":
      return { text: "✗ research failed", tone: "error" };
    default:
      return { text: event.phase, tone: "default" };
  }
}

function toneClass(tone: LineTone): string {
  switch (tone) {
    case "warn":
      return "text-[var(--c-amber)]";
    case "error":
      return "text-[var(--c-red)]";
    case "success":
      return "text-[var(--c-accent)]";
    default:
      return "text-[var(--fg-dim)]";
  }
}

export function ResearchActivityFeed({
  events,
  status,
  className,
}: {
  events: ResearchActivityEvent[];
  status: string;
  className?: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const isActive = status === "running" || status === "queued";

  const lines = useMemo(() => {
    const visible = events.slice(-VISIBLE_EVENTS);
    return visible
      .map((event, index) => {
        const formatted = formatEventLine(event);
        if (formatted.skip) return null;
        return {
          key: `${event.ts ?? "evt"}-${event.phase}-${index}`,
          ...formatted,
          isLatest: index === visible.length - 1,
        };
      })
      .filter((line): line is NonNullable<typeof line> => line !== null);
  }, [events]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lines.length, events.length]);

  if (lines.length === 0 && !isActive) {
    return null;
  }

  return (
    <div
      className={cn(
        "h-36 overflow-y-auto overflow-x-hidden rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5",
        className,
      )}
      ref={scrollRef}
      aria-live="polite"
      aria-label="Research activity"
    >
      {lines.length === 0 ? (
        <p className="font-mono text-[10px] text-[var(--fg-faint)]">Waiting for activity…</p>
      ) : (
        <ul className="space-y-0.5">
          {lines.map((line) => (
            <li
              key={line.key}
              className={cn(
                "flex min-w-0 items-start gap-1 font-mono text-[10px] leading-snug transition-all duration-200 ease-out animate-in fade-in slide-in-from-bottom-0.5",
                toneClass(line.tone),
              )}
            >
              <span className="min-w-0 truncate">{line.text}</span>
              {isActive && line.isLatest ? (
                <span
                  className="mt-0.5 inline-block size-1.5 shrink-0 animate-[research-cursor-pulse_1s_ease-in-out_infinite] bg-[var(--c-blue)]"
                  aria-hidden
                />
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
