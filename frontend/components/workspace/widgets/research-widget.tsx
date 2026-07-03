"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { ResearchViewerModal } from "@/components/research/research-viewer-modal";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { Skeleton } from "@/components/ui/skeleton";
import {
  TerminalButton,
  TerminalField,
  TerminalInput,
  TerminalSelect,
} from "@/components/ui/terminal";
import {
  EFFORT_LABELS,
  getResearchConfig,
  getResearchJob,
  listResearchArtifacts,
  runResearch,
  type ResearchArtifact,
  type ResearchDepth,
} from "@/lib/research";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 5_000;
const LIST_LIMIT = 10;

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

export function ResearchWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [topic, setTopic] = useState("");
  const [symbol, setSymbol] = useState("");
  const [depth, setDepth] = useState<ResearchDepth>("standard");
  const [effortPresets, setEffortPresets] = useState<ResearchDepth[]>([
    "quick",
    "standard",
    "deep",
    "exhaustive",
  ]);
  const [running, setRunning] = useState(false);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const [viewerId, setViewerId] = useState<number | null>(null);
  const [viewerOpen, setViewerOpen] = useState(false);

  const refreshList = useCallback(async () => {
    try {
      const items = await listResearchArtifacts(LIST_LIMIT);
      setArtifacts(items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load research");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    let cancelled = false;
    void getResearchConfig()
      .then((cfg) => {
        if (cancelled) return;
        const presets = (cfg.effort_presets ?? []).filter(
          (p): p is ResearchDepth =>
            p === "quick" ||
            p === "standard" ||
            p === "deep" ||
            p === "exhaustive",
        );
        if (presets.length) setEffortPresets(presets);
        const defaultEffort = cfg.current?.effort;
        if (
          defaultEffort === "quick" ||
          defaultEffort === "standard" ||
          defaultEffort === "deep" ||
          defaultEffort === "exhaustive"
        ) {
          setDepth(defaultEffort);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;

    async function poll() {
      try {
        const job = await getResearchJob(activeJobId!);
        if (cancelled) return;
        setRunStatus(job.status);
        if (job.status === "completed") {
          const artifactId = job.result?.artifact_id;
          setActiveJobId(null);
          setRunning(false);
          await refreshList();
          if (artifactId) {
            setViewerId(artifactId);
            setViewerOpen(true);
          }
        } else if (job.status === "failed" || job.status === "cancelled") {
          setActiveJobId(null);
          setRunning(false);
          setError(job.error ?? `Research ${job.status}`);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Job poll failed");
          setActiveJobId(null);
          setRunning(false);
        }
      }
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [activeJobId, refreshList]);

  async function handleRun() {
    const trimmedTopic = topic.trim();
    if (!trimmedTopic) return;
    setRunning(true);
    setError(null);
    setRunStatus("queued");
    try {
      const queued = await runResearch({
        topic: trimmedTopic,
        symbol: symbol.trim() || undefined,
        depth,
      });
      setActiveJobId(queued.job_id);
      setRunStatus(queued.status);
    } catch (err) {
      setRunning(false);
      setRunStatus(null);
      setError(err instanceof Error ? err.message : "Research failed");
    }
  }

  function openViewer(id: number) {
    setViewerId(id);
    setViewerOpen(true);
  }

  if (loading && artifacts.length === 0) {
    return <Skeleton className="m-3 h-full min-h-24 rounded-md" />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden px-1.5 py-1">
      <div className="shrink-0 border-b border-[var(--border)] px-1 pb-2">
        <p className="mb-2 font-mono text-[11px] tracking-[0.04em] text-[var(--fg-faint)] uppercase">
          Research
        </p>
        <div className="space-y-2">
          <TerminalField label="Topic" htmlFor="research-topic">
            <TerminalInput
              id="research-topic"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="NVDA earnings setup"
              disabled={running}
            />
          </TerminalField>
          <div className="grid grid-cols-2 gap-2">
            <TerminalField label="Symbol" htmlFor="research-symbol">
              <TerminalInput
                id="research-symbol"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="AAPL"
                disabled={running}
              />
            </TerminalField>
            <TerminalField label="Effort" htmlFor="research-effort">
              <TerminalSelect
                id="research-effort"
                value={depth}
                onChange={(e) => setDepth(e.target.value as ResearchDepth)}
                disabled={running}
              >
                {effortPresets.map((preset) => (
                  <option key={preset} value={preset}>
                    {EFFORT_LABELS[preset] ?? preset}
                  </option>
                ))}
              </TerminalSelect>
            </TerminalField>
          </div>
          <TerminalButton
            variant="primary"
            disabled={running || !topic.trim()}
            onClick={() => void handleRun()}
            className="w-full"
          >
            {running ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 className="size-3 animate-spin" />
                {runStatus ?? "Running"}…
              </span>
            ) : (
              "Run research"
            )}
          </TerminalButton>
        </div>
      </div>

      {error ? (
        <p className="shrink-0 border-b border-[var(--border)] px-1 py-1.5 font-mono text-[10px] text-[var(--c-red)]">
          {error}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto pt-1">
        <p className="mb-1 px-1 font-mono text-[10px] tracking-[0.04em] text-[var(--fg-faint)] uppercase">
          Recent
        </p>
        {artifacts.length === 0 ? (
          <p className="px-1 font-mono text-[11px] text-[var(--fg-dim)]">
            No research runs yet.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {artifacts.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => openViewer(item.id)}
                  className="group/row w-full rounded border border-transparent px-1.5 py-1.5 text-left transition-colors hover:border-[var(--border)] hover:bg-[var(--bg-hover)]"
                >
                  <p className="line-clamp-2 font-mono text-[11px] text-foreground">
                    {item.topic}
                  </p>
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
                    {item.symbol ? (
                      <ClickableSymbol
                        symbol={item.symbol}
                        linked={linked}
                        onActivate={() => publish(item.symbol!)}
                        className="text-[10px]"
                      />
                    ) : null}
                    <span className="text-[var(--fg-dim)]">{item.depth}</span>
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ResearchViewerModal
        artifactId={viewerId}
        open={viewerOpen}
        onClose={() => setViewerOpen(false)}
      />
    </div>
  );
}
