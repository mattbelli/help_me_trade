"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, ExternalLink, FileText, Loader2 } from "lucide-react";

import { downloadResearchMarkdown } from "@/components/research/research-download";
import { ResearchMarkdown } from "@/components/research/research-markdown";
import { TerminalButton } from "@/components/ui/terminal";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getResearchArtifact, researchHtmlUrl, type ResearchArtifact } from "@/lib/research";
import { cn } from "@/lib/utils";

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

function formatDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return iso;
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function ResearchViewerModal({
  artifactId,
  open,
  onClose,
}: {
  artifactId: number | null;
  open: boolean;
  onClose: () => void;
}) {
  const [artifact, setArtifact] = useState<ResearchArtifact | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"markdown" | "raw">("markdown");

  useEffect(() => {
    if (!open || artifactId == null) {
      setArtifact(null);
      setError(null);
      setView("markdown");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void getResearchArtifact(artifactId)
      .then((data) => {
        if (!cancelled) setArtifact(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load research");
          setArtifact(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, artifactId]);

  const handleDownload = useCallback(() => {
    if (!artifact?.markdown) return;
    downloadResearchMarkdown(artifact);
  }, [artifact]);

  const title = artifact?.topic ?? "Research report";

  const metaLine = useMemo(() => {
    if (!artifact) return null;
    const parts = [artifact.depth, formatDate(artifact.created_at)];
    if (artifact.sources?.length) parts.push(`${artifact.sources.length} sources`);
    return parts.join(" · ");
  }, [artifact]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        showCloseButton
        className={cn(
          "flex max-h-[90vh] w-full flex-col gap-0 overflow-hidden border border-[var(--border)] bg-[var(--panel)] p-0 font-mono text-foreground",
          "sm:max-w-3xl",
          "max-md:fixed max-md:inset-0 max-md:top-0 max-md:left-0 max-md:h-full max-md:max-h-full max-md:w-full max-md:max-w-full max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none",
        )}
      >
        <DialogHeader className="shrink-0 gap-2 border-b border-[var(--border)] px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-2 pr-8">
            <div className="min-w-0 flex-1">
              <DialogTitle className="line-clamp-2 font-mono text-[13px] leading-snug">
                {title}
              </DialogTitle>
              {artifact && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  {artifact.symbol ? (
                    <span className="rounded border border-[var(--border)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--c-accent)]">
                      {artifact.symbol}
                    </span>
                  ) : null}
                  <span
                    className={cn(
                      "rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase",
                      statusChipClass(artifact.status),
                    )}
                  >
                    {artifact.status}
                  </span>
                  {metaLine ? (
                    <span className="font-mono text-[10px] text-[var(--fg-faint)]">
                      {metaLine}
                    </span>
                  ) : null}
                </div>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <TerminalButton
              variant={view === "markdown" ? "primary" : "default"}
              className="text-[10px]"
              onClick={() => setView("markdown")}
            >
              <FileText className="size-3" />
              Preview
            </TerminalButton>
            <TerminalButton
              variant={view === "raw" ? "primary" : "default"}
              className="text-[10px]"
              onClick={() => setView("raw")}
            >
              Raw
            </TerminalButton>
            {artifact?.markdown ? (
              <TerminalButton
                variant="default"
                className="text-[10px]"
                onClick={handleDownload}
              >
                <Download className="size-3" />
                Download .md
              </TerminalButton>
            ) : null}
            {artifact?.html ? (
              <a
                href={researchHtmlUrl(artifact.id)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded border border-[var(--border)] px-2 py-1 font-mono text-[10px] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
              >
                <ExternalLink className="size-3" />
                HTML view
              </a>
            ) : null}
            <TerminalButton variant="default" className="ml-auto text-[10px]" onClick={onClose}>
              Close
            </TerminalButton>
          </div>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-12 text-[11px] text-[var(--fg-dim)]">
              <Loader2 className="size-4 animate-spin" />
              Loading report…
            </div>
          ) : error ? (
            <p className="py-8 text-center text-[11px] text-[var(--c-red)]">{error}</p>
          ) : artifact?.markdown && view === "markdown" ? (
            <ResearchMarkdown markdown={artifact.markdown} />
          ) : artifact?.markdown && view === "raw" ? (
            <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[var(--fg-dim)]">
              {artifact.markdown}
            </pre>
          ) : artifact ? (
            <p className="py-8 text-center text-[11px] text-[var(--fg-dim)]">
              No markdown content yet.
            </p>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
