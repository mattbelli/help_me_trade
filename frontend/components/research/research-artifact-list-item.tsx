"use client";

import { Download, Trash2 } from "lucide-react";

import { downloadResearchMarkdown } from "@/components/research/research-download";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import type { ResearchArtifact } from "@/lib/research";
import { cn } from "@/lib/utils";

function formatRelativeTime(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return iso;
  const deltaSec = Math.round((Date.now() - ts) / 1000);
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusChipClass(status: string): string {
  switch (status) {
    case "completed":
      return "text-[var(--c-accent)] border-[color-mix(in_srgb,var(--c-accent)_35%,transparent)]";
    case "running":
    case "queued":
      return "text-[var(--c-blue)] border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)]";
    case "failed":
      return "text-[var(--c-red)] border-[color-mix(in_srgb,var(--c-red)_35%,transparent)]";
    default:
      return "text-[var(--fg-dim)] border-[var(--border)]";
  }
}

export function ResearchArtifactListItem({
  item,
  selected = false,
  onSelect,
  onDelete,
  variant = "page",
  linked,
  onSymbolActivate,
}: {
  item: ResearchArtifact;
  selected?: boolean;
  onSelect: () => void;
  onDelete: (id: number) => void;
  variant?: "page" | "widget";
  linked?: boolean;
  onSymbolActivate?: (symbol: string) => void;
}) {
  if (variant === "widget") {
    return (
      <li className="group/row">
        <div
          className={cn(
            "flex items-start gap-0.5 rounded border border-transparent px-1.5 py-1.5 transition-colors hover:border-[var(--border)] hover:bg-[var(--bg-hover)]",
            selected && "border-[var(--border)] bg-[var(--bg-hover)]",
          )}
        >
          <button type="button" onClick={onSelect} className="min-w-0 flex-1 text-left">
            <p className="line-clamp-2 font-mono text-[11px] text-foreground">{item.topic}</p>
            <p className="mt-0.5 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-[var(--fg-faint)]">
              <span
                className={cn(
                  "rounded border px-1 uppercase",
                  statusChipClass(item.status),
                )}
              >
                {item.status}
              </span>
              <span>{formatRelativeTime(item.created_at)}</span>
              {item.symbol && onSymbolActivate ? (
                <ClickableSymbol
                  symbol={item.symbol}
                  linked={linked ?? false}
                  onActivate={() => onSymbolActivate(item.symbol!)}
                  className="text-[10px]"
                />
              ) : item.symbol ? (
                <span>{item.symbol}</span>
              ) : null}
              <span className="text-[var(--fg-dim)]">{item.depth}</span>
            </p>
          </button>
          <div className="flex shrink-0 flex-col gap-0.5 opacity-0 transition-opacity group-hover/row:opacity-100">
            {item.markdown ? (
              <button
                type="button"
                title="Download markdown"
                aria-label={`Download research: ${item.topic}`}
                className="rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--c-accent)]"
                onClick={(e) => {
                  e.stopPropagation();
                  downloadResearchMarkdown(item);
                }}
              >
                <Download className="size-3" />
              </button>
            ) : null}
            <button
              type="button"
              title="Delete research"
              aria-label={`Delete research: ${item.topic}`}
              className="rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(item.id);
              }}
            >
              <Trash2 className="size-3" />
            </button>
          </div>
        </div>
      </li>
    );
  }

  return (
    <div
      className={cn(
        "flex items-start gap-1 rounded-lg border px-1 py-1 transition-colors",
        selected
          ? "border-accent/40 bg-accent/10"
          : "border-transparent hover:bg-muted/50",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        className="min-w-0 flex-1 rounded-md px-2 py-1.5 text-left"
      >
        <div className="flex items-start justify-between gap-2">
          <p className="line-clamp-2 text-sm font-medium">{item.topic}</p>
          <Badge variant="outline" className="shrink-0 text-[10px] uppercase">
            {item.depth}
          </Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {new Date(item.created_at).toLocaleString()}
          {item.symbol ? ` · ${item.symbol}` : ""}
        </p>
      </button>
      {item.markdown ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={`Download research: ${item.topic}`}
          onClick={() => downloadResearchMarkdown(item)}
        >
          <Download className="size-3.5" />
        </Button>
      ) : null}
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="shrink-0 text-muted-foreground hover:text-destructive"
        aria-label={`Delete research: ${item.topic}`}
        onClick={() => onDelete(item.id)}
      >
        <Trash2 className="size-3.5" />
      </Button>
    </div>
  );
}
