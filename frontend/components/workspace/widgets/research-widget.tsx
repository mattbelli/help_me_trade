"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { ResearchActivityFeed } from "@/components/research/research-activity-feed";
import { ResearchArtifactListItem } from "@/components/research/research-artifact-list-item";
import { ResearchViewerModal } from "@/components/research/research-viewer-modal";
import { RESEARCH_DELETE_CONFIRM } from "@/components/research/research-viewer";
import {
  WidgetEmpty,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  TerminalButton,
  TerminalField,
  TerminalInput,
  TerminalSelect,
} from "@/components/ui/terminal";
import {
  deleteResearchArtifact,
  EFFORT_LABELS,
  getResearchConfig,
  listResearchArtifacts,
  type ResearchArtifact,
  type ResearchDepth,
} from "@/lib/research";
import { useResearchJobFeed } from "@/lib/use-research-job-feed";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const LIST_LIMIT = 10;

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

  const {
    isRunning: running,
    activityEvents,
    error: feedError,
    setError: setFeedError,
    startResearch,
  } = useResearchJobFeed({
    onComplete: async (artifactId) => {
      await refreshList();
      setViewerId(artifactId);
      setViewerOpen(true);
    },
  });

  const displayError = feedError ?? error;

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

  async function handleRun() {
    const trimmedTopic = topic.trim();
    if (!trimmedTopic) return;
    setError(null);
    setFeedError(null);
    await startResearch({
      topic: trimmedTopic,
      symbol: symbol.trim() || undefined,
      depth,
    });
  }

  async function handleDelete(id: number) {
    if (!window.confirm(RESEARCH_DELETE_CONFIRM)) return;
    setError(null);
    try {
      await deleteResearchArtifact(id);
      if (viewerId === id) {
        setViewerId(null);
        setViewerOpen(false);
      }
      await refreshList();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete research");
    }
  }

  function openViewer(id: number) {
    setViewerId(id);
    setViewerOpen(true);
  }

  if (loading && artifacts.length === 0) {
    return <WidgetLoading rows={3} cols={[2, 1]} />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden px-1.5 py-1">
      <div className="shrink-0 border-b border-[var(--border)] px-1 pb-2">
        <p className="mb-2 font-mono text-[length:var(--fs-body)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
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
                Running…
              </span>
            ) : (
              "Run research"
            )}
          </TerminalButton>
          {running ? (
            <ResearchActivityFeed
              events={activityEvents}
              status="running"
              className="mt-2 max-h-28"
            />
          ) : null}
        </div>
      </div>

      {displayError ? (
        <p className="shrink-0 border-b border-[var(--border)] px-1 py-1.5 font-mono text-[length:var(--fs-table)] text-[var(--c-red)]">
          {displayError}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto pt-1">
        <p className="mb-1 px-1 font-mono text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
          Recent
        </p>
        {artifacts.length === 0 ? (
          <WidgetEmpty message="No research runs yet." className="px-1 py-0" />
        ) : (
          <ul className="space-y-0.5">
            {artifacts.map((item) => (
              <ResearchArtifactListItem
                key={item.id}
                item={item}
                variant="widget"
                linked={linked}
                onSymbolActivate={publish}
                onSelect={() => openViewer(item.id)}
                onDelete={(id) => void handleDelete(id)}
              />
            ))}
          </ul>
        )}
      </div>

      <ResearchViewerModal
        artifactId={viewerId}
        open={viewerOpen}
        onClose={() => setViewerOpen(false)}
        onDeleted={() => void refreshList()}
      />
    </div>
  );
}
