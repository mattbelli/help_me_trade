"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FileSearch, Loader2, Sparkles } from "lucide-react";

import { ResearchActivityFeed } from "@/components/research/research-activity-feed";
import { ResearchArtifactListItem } from "@/components/research/research-artifact-list-item";
import {
  RESEARCH_DELETE_CONFIRM,
  ResearchViewerActionBar,
  ResearchViewerBody,
  ResearchViewerFooter,
  useResearchViewMode,
} from "@/components/research/research-viewer";

import { PagesMenu } from "@/components/layout/pages-menu";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  deleteResearchArtifact,
  EFFORT_LABELS,
  getResearchArtifact,
  getResearchConfig,
  listResearchArtifacts,
  RECIPE_LABELS,
  type ResearchArtifact,
  type ResearchConfig,
  type ResearchDepth,
  type ResearchRecipe,
} from "@/lib/research";
import { useResearchJobFeed } from "@/lib/use-research-job-feed";
import { TERMINAL_COLLAPSED_HEIGHT } from "@/lib/workspace/use-terminal-store";

const PHASE_LABELS: Record<string, string> = {
  started: "Starting research run",
  planning: "Planning search queries",
  scan_candidates: "Resolving scan candidates",
  candidate_started: "Assessing candidate",
  candidate_done: "Candidate assessed",
  ranking: "Ranking setups",
  searching: "Gathering sources",
  sources: "Sources collected",
  analyzing: "Analyzing findings",
  evaluating: "Evaluating coverage",
  writing: "Writing report",
  completed: "Report ready",
};

export function ResearchPage() {
  const searchParams = useSearchParams();
  const deepLinkId = Number(searchParams.get("id"));
  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(
    Number.isFinite(deepLinkId) && deepLinkId > 0 ? deepLinkId : null,
  );
  const [selected, setSelected] = useState<ResearchArtifact | null>(null);
  const [topic, setTopic] = useState("AAPL momentum and sector context");
  const [symbol, setSymbol] = useState("AAPL");
  const [scanSymbols, setScanSymbols] = useState("");
  const [recipe, setRecipe] = useState<ResearchRecipe>("single_stock");
  const [depth, setDepth] = useState<ResearchDepth>("standard");
  const [researchConfig, setResearchConfig] = useState<ResearchConfig | null>(null);
  const [effortPresets, setEffortPresets] = useState<ResearchDepth[]>([
    "quick",
    "standard",
    "deep",
    "exhaustive",
  ]);
  const [viewMode, setViewMode] = useResearchViewMode(selected, selectedId);

  const {
    isRunning: loading,
    activityEvents,
    phase,
    phaseDetail,
    streamWarning,
    error,
    setError,
    startResearch,
  } = useResearchJobFeed({
    onComplete: async (artifactId) => {
      await refreshListRef.current?.();
      setSelectedId(artifactId);
    },
  });

  const refreshListRef = useRef<(() => Promise<void>) | null>(null);

  const refreshList = useCallback(async () => {
    try {
      const items = await listResearchArtifacts();
      setArtifacts(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load research");
    }
  }, [setError]);

  refreshListRef.current = refreshList;

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    void getResearchConfig()
      .then((cfg) => {
        setResearchConfig(cfg);
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
      .catch(() => setResearchConfig(null));
  }, []);

  useEffect(() => {
    if (Number.isFinite(deepLinkId) && deepLinkId > 0) {
      setSelectedId(deepLinkId);
    }
  }, [deepLinkId]);

  useEffect(() => {
    if (selectedId == null) {
      setSelected(null);
      return;
    }
    void getResearchArtifact(selectedId)
      .then((data) => {
        setSelected(data);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load artifact"),
      );
  }, [selectedId]);

  async function handleRun() {
    setError(null);
    const isSetupScan = recipe === "setup_scan";
    const defaultTopic = `Setup scan — ${new Date().toISOString().slice(0, 10)}`;
    const resolvedTopic = isSetupScan
      ? topic.trim() || defaultTopic
      : topic.trim();
    const parsedSymbols = isSetupScan
      ? scanSymbols
          .split(/[\s,]+/)
          .map((s) => s.trim().toUpperCase())
          .filter(Boolean)
      : undefined;
    await startResearch({
      topic: resolvedTopic,
      symbol: isSetupScan ? undefined : symbol.trim() || undefined,
      depth,
      recipe,
      symbols: parsedSymbols?.length ? parsedSymbols : undefined,
    });
  }

  async function handleDelete(id: number) {
    if (!window.confirm(RESEARCH_DELETE_CONFIRM)) {
      return;
    }
    setError(null);
    try {
      await deleteResearchArtifact(id);
      if (selectedId === id) {
        setSelectedId(null);
        setSelected(null);
      }
      await refreshList();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete research");
    }
  }

  return (
    <div className="flex h-svh min-h-0 flex-col bg-background">
      <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-[var(--border)] bg-[var(--bg-raised)] px-3.5 sm:px-4">
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          <PagesMenu />
          <div className="min-w-0">
            <h1 className="truncate font-mono text-[length:var(--fs-title)] font-semibold tracking-tight text-foreground">
              Research
            </h1>
            <p className="truncate text-[length:var(--fs-table)] text-[var(--fg-dim)]">
              Deep research with iterative search and structured reports
            </p>
          </div>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-auto">
        <div
          className="mx-auto w-full max-w-[1920px] space-y-6 px-4 py-5 sm:px-6 sm:py-6"
          // Reserve the collapsed terminal strip's height so the last content
          // isn't hidden under the fixed viewport overlay (workspace does this).
          style={{ paddingBottom: `calc(1.5rem + ${TERMINAL_COLLAPSED_HEIGHT}px)` }}
        >
          {error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {researchConfig?.uses_mock_web && (
            <div
              className="rounded-lg border border-[var(--c-amber)]/40 bg-[var(--c-amber)]/10 px-4 py-3 text-sm text-[var(--c-amber)]"
              role="alert"
            >
              <strong className="font-medium text-[var(--foreground)]">Mock web search active.</strong>{" "}
              {researchConfig.warning ??
                "Configure SearXNG in Settings or set SEARXNG_URL — reports will use placeholder snippets until then."}
            </div>
          )}

          {streamWarning && !researchConfig?.uses_mock_web && (
            <div className="rounded-lg border border-[var(--c-amber)]/40 bg-[var(--c-amber)]/10 px-4 py-3 text-sm text-[var(--c-amber)]">
              {streamWarning}
            </div>
          )}

          <div className="grid gap-6 xl:grid-cols-[340px_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="size-4 text-accent" />
                New research
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Recipe</Label>
                <Select
                  value={recipe}
                  onValueChange={(v) => setRecipe(v as ResearchRecipe)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.keys(RECIPE_LABELS) as ResearchRecipe[]).map((r) => (
                      <SelectItem key={r} value={r}>
                        {RECIPE_LABELS[r]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="topic">
                  Topic{recipe === "setup_scan" ? " (optional)" : ""}
                </Label>
                <Input
                  id="topic"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder={
                    recipe === "setup_scan"
                      ? "Leave blank for default date title"
                      : "NVDA earnings setup and sector risks"
                  }
                />
              </div>
              {recipe === "single_stock" ? (
                <div className="space-y-2">
                  <Label htmlFor="symbol">Symbol (optional)</Label>
                  <Input
                    id="symbol"
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                    placeholder="AAPL"
                  />
                </div>
              ) : (
                <div className="space-y-2">
                  <Label htmlFor="scanSymbols">Symbols (optional)</Label>
                  <Input
                    id="scanSymbols"
                    value={scanSymbols}
                    onChange={(e) => setScanSymbols(e.target.value.toUpperCase())}
                    placeholder="blank = active watchlist"
                  />
                </div>
              )}
              <div className="space-y-2">
                <Label>Effort</Label>
                <Select
                  value={depth}
                  onValueChange={(v) => setDepth(v as ResearchDepth)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {effortPresets.map((d) => (
                      <SelectItem key={d} value={d}>
                        {EFFORT_LABELS[d] ?? d}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                className="w-full"
                onClick={() => void handleRun()}
                disabled={loading || (recipe === "single_stock" && !topic.trim())}
              >
                {loading ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Running…
                  </>
                ) : (
                  <>
                    <FileSearch className="size-4" />
                    {recipe === "setup_scan" ? "Run setup scan" : "Run deep research"}
                  </>
                )}
              </Button>
              {loading && phase && (
                <p className="text-xs text-muted-foreground">
                  {PHASE_LABELS[phase] ?? phase}
                  {phaseDetail ? ` · ${phaseDetail}` : ""}
                </p>
              )}
              {loading ? (
                <ResearchActivityFeed events={activityEvents} status="running" />
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent reports</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <ScrollArea className="h-[420px]">
                <div className="space-y-1 px-4 pb-4">
                  {artifacts.length === 0 && (
                    <p className="py-6 text-center text-sm text-muted-foreground">
                      No research runs yet.
                    </p>
                  )}
                  {artifacts.map((item) => (
                    <ResearchArtifactListItem
                      key={item.id}
                      item={item}
                      selected={selectedId === item.id}
                      onSelect={() => setSelectedId(item.id)}
                      onDelete={(id) => void handleDelete(id)}
                    />
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        <Card className="min-h-[640px]">
          <CardHeader className="flex-row items-center justify-between gap-4 border-b">
            <div>
              <CardTitle>
                {selected?.topic ?? "Report viewer"}
              </CardTitle>
              {selected && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {selected.depth} depth · {selected.sources?.length ?? 0} sources
                  {selected.report?.web_search_warning ? (
                    <span className="text-[var(--c-amber)]"> · mock web sources</span>
                  ) : null}
                </p>
              )}
            </div>
            <ResearchViewerActionBar
              artifact={selected}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              onDelete={(id) => void handleDelete(id)}
            />
          </CardHeader>
          <CardContent className="p-0">
            <ResearchViewerBody artifact={selected} viewMode={viewMode} />
            {selected ? <ResearchViewerFooter artifact={selected} /> : null}
          </CardContent>
        </Card>
          </div>
        </div>
      </main>
    </div>
  );
}
