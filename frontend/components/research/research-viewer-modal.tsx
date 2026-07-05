"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import {
  RESEARCH_DELETE_CONFIRM,
  ResearchViewerActionBar,
  ResearchViewerBody,
  useResearchViewMode,
} from "@/components/research/research-viewer";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  deleteResearchArtifact,
  getResearchArtifact,
  type ResearchArtifact,
} from "@/lib/research";
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
  onDeleted,
}: {
  artifactId: number | null;
  open: boolean;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const [artifact, setArtifact] = useState<ResearchArtifact | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useResearchViewMode(artifact, artifactId);

  useEffect(() => {
    if (!open || artifactId == null) {
      setArtifact(null);
      setError(null);
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

  const title = artifact?.topic ?? "Research report";

  const metaLine = useMemo(() => {
    if (!artifact) return null;
    const parts = [artifact.depth, formatDate(artifact.created_at)];
    if (artifact.sources?.length) parts.push(`${artifact.sources.length} sources`);
    return parts.join(" · ");
  }, [artifact]);

  async function handleDelete(id: number) {
    if (!window.confirm(RESEARCH_DELETE_CONFIRM)) return;
    try {
      await deleteResearchArtifact(id);
      onDeleted?.();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete research");
    }
  }

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
              <DialogTitle className="line-clamp-2 font-mono text-[length:var(--fs-title)] leading-snug">
                {title}
              </DialogTitle>
              {artifact && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  {artifact.symbol ? (
                    <span className="rounded border border-[var(--border)] px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] text-[var(--c-accent)]">
                      {artifact.symbol}
                    </span>
                  ) : null}
                  <span
                    className={cn(
                      "rounded border px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] uppercase",
                      statusChipClass(artifact.status),
                    )}
                  >
                    {artifact.status}
                  </span>
                  {metaLine ? (
                    <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                      {metaLine}
                    </span>
                  ) : null}
                </div>
              )}
            </div>
          </div>

          {artifact ? (
            <ResearchViewerActionBar
              artifact={artifact}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              onDelete={(id) => void handleDelete(id)}
            />
          ) : null}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-auto">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-12 text-[length:var(--fs-body)] text-[var(--fg-dim)]">
              <Loader2 className="size-4 animate-spin" />
              Loading report…
            </div>
          ) : error ? (
            <p className="py-8 text-center text-[length:var(--fs-body)] text-[var(--c-red)]">{error}</p>
          ) : (
            <ResearchViewerBody
              artifact={artifact}
              viewMode={viewMode}
              bodyClassName="min-h-[240px]"
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
