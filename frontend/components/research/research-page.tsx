"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, ExternalLink, FileSearch, FileText, Loader2, Sparkles, Trash2 } from "lucide-react";

import { downloadResearchMarkdown } from "@/components/research/research-download";
import { ResearchActivityFeed } from "@/components/research/research-activity-feed";
import { ResearchMarkdown } from "@/components/research/research-markdown";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
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
import { Separator } from "@/components/ui/separator";
import { apiFetch } from "@/lib/api";
import {
  buildReportThemeOverride,
  injectReportTheme,
} from "@/lib/report-theme";
import {
  deleteResearchArtifact,
  EFFORT_LABELS,
  getResearchArtifact,
  getResearchConfig,
  listResearchArtifacts,
  RECIPE_LABELS,
  researchHtmlUrl,
  streamResearch,
  type ResearchActivityEvent,
  type ResearchArtifact,
  type ResearchConfig,
  type ResearchDepth,
  type ResearchRecipe,
} from "@/lib/research";
import { THEME_VARS_EVENT } from "@/lib/workspace/custom-themes";

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

function ResearchReportViewer({
  artifactId,
  html,
  title,
}: {
  artifactId: number;
  html: string | null;
  title: string;
}) {
  const [rawHtml, setRawHtml] = useState<string | null>(html);
  const [themeNonce, setThemeNonce] = useState(0);

  useEffect(() => {
    setRawHtml(html);
  }, [html, artifactId]);

  useEffect(() => {
    if (rawHtml) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await apiFetch(researchHtmlUrl(artifactId), { cache: "no-store" });
        if (!res.ok || cancelled) return;
        setRawHtml(await res.text());
      } catch {
        if (!cancelled) setRawHtml(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [artifactId, rawHtml]);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const el = document.documentElement;
    const obs = new MutationObserver(() => setThemeNonce((n) => n + 1));
    obs.observe(el, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });
    const onVars = () => setThemeNonce((n) => n + 1);
    window.addEventListener(THEME_VARS_EVENT, onVars);
    return () => {
      obs.disconnect();
      window.removeEventListener(THEME_VARS_EVENT, onVars);
    };
  }, []);

  const themedSrcDoc = useMemo(() => {
    if (!rawHtml) return "";
    if (typeof document === "undefined") return rawHtml;
    return injectReportTheme(rawHtml, buildReportThemeOverride());
  }, [rawHtml, themeNonce]);

  if (!rawHtml) {
    return (
      <div className="flex h-[560px] items-center justify-center text-sm text-muted-foreground">
        Loading report…
      </div>
    );
  }

  return (
    <iframe
      title={title}
      srcDoc={themedSrcDoc}
      className="h-[560px] w-full border-0 bg-[var(--bg-inset)]"
      sandbox="allow-same-origin"
    />
  );
}

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [phaseDetail, setPhaseDetail] = useState<string>("");
  const [activityEvents, setActivityEvents] = useState<ResearchActivityEvent[]>([]);
  const [researchConfig, setResearchConfig] = useState<ResearchConfig | null>(null);
  const [streamWarning, setStreamWarning] = useState<string | null>(null);
  const [effortPresets, setEffortPresets] = useState<ResearchDepth[]>([
    "quick",
    "standard",
    "deep",
    "exhaustive",
  ]);
  const [viewMode, setViewMode] = useState<"markdown" | "html" | "raw">("markdown");

  const refreshList = useCallback(async () => {
    try {
      const items = await listResearchArtifacts();
      setArtifacts(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load research");
    }
  }, []);

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
    setViewMode("markdown");
    void getResearchArtifact(selectedId)
      .then((data) => {
        setSelected(data);
        if (!data.markdown?.trim() && data.html) {
          setViewMode("html");
        }
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load artifact"),
      );
  }, [selectedId]);

  async function handleRun() {
    setLoading(true);
    setError(null);
    setPhase("started");
    setPhaseDetail("");
    setActivityEvents([]);
    setStreamWarning(null);
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
    try {
      const result = await streamResearch(
        {
          topic: resolvedTopic,
          symbol: isSetupScan ? undefined : symbol.trim() || undefined,
          depth,
          recipe,
          symbols: parsedSymbols?.length ? parsedSymbols : undefined,
        },
        (event) => {
          setPhase(event.phase);
          setActivityEvents((prev) => [
            ...prev,
            {
              ts: new Date().toISOString(),
              phase: event.phase,
              ...(event.data as Omit<ResearchActivityEvent, "ts" | "phase">),
            },
          ]);
          if (event.phase === "web_warning" && event.data.warning) {
            setStreamWarning(String(event.data.warning));
          } else if (event.phase === "searching" && event.data.query) {
            setPhaseDetail(String(event.data.query));
          } else if (event.phase === "sources" && event.data.total) {
            setPhaseDetail(`${event.data.total} sources`);
          } else if (event.phase === "writing") {
            setPhaseDetail(`${event.data.findings ?? 0} findings`);
          }
        },
      );
      await refreshList();
      if (result?.artifact_id) {
        setSelectedId(result.artifact_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Research failed");
    } finally {
      setLoading(false);
      setPhase(null);
      setPhaseDetail("");
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm("Delete this research report? This cannot be undone.")) {
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
    <div className="space-y-8">
      <PageHeader
        title="Research"
        subtitle="Odysseus-style deep research: iterative search, source analysis, and structured HTML reports."
      />

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
                    <div
                      key={item.id}
                      className={`flex items-start gap-1 rounded-lg border px-1 py-1 transition-colors ${
                        selectedId === item.id
                          ? "border-accent/40 bg-accent/10"
                          : "border-transparent hover:bg-muted/50"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => setSelectedId(item.id)}
                        className="min-w-0 flex-1 rounded-md px-2 py-1.5 text-left"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <p className="line-clamp-2 text-sm font-medium">
                            {item.topic}
                          </p>
                          <Badge variant="outline" className="shrink-0 text-[10px] uppercase">
                            {item.depth}
                          </Badge>
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {new Date(item.created_at).toLocaleString()}
                          {item.symbol ? ` · ${item.symbol}` : ""}
                        </p>
                      </button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="shrink-0 text-muted-foreground hover:text-destructive"
                        aria-label={`Delete research: ${item.topic}`}
                        onClick={() => void handleDelete(item.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
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
            <div className="flex flex-wrap items-center gap-2">
              {selected?.markdown ? (
                <>
                  <Button
                    type="button"
                    variant={viewMode === "markdown" ? "default" : "outline"}
                    size="sm"
                    onClick={() => setViewMode("markdown")}
                  >
                    <FileText className="size-3.5" />
                    Preview
                  </Button>
                  <Button
                    type="button"
                    variant={viewMode === "raw" ? "default" : "outline"}
                    size="sm"
                    onClick={() => setViewMode("raw")}
                  >
                    Raw
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => downloadResearchMarkdown(selected)}
                  >
                    <Download className="size-3.5" />
                    Download .md
                  </Button>
                </>
              ) : null}
              {selected?.html ? (
                <>
                  <Button
                    type="button"
                    variant={viewMode === "html" ? "default" : "outline"}
                    size="sm"
                    onClick={() => setViewMode("html")}
                  >
                    HTML
                  </Button>
                  <a
                    href={researchHtmlUrl(selected.id)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex h-7 items-center gap-1 rounded-[min(var(--radius-md),12px)] border border-border bg-background px-2.5 text-[0.8rem] font-medium hover:bg-muted"
                  >
                    <ExternalLink className="size-3.5" />
                    Open full report
                  </a>
                </>
              ) : null}
              {selected ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  onClick={() => void handleDelete(selected.id)}
                >
                  <Trash2 className="size-3.5" />
                  Delete
                </Button>
              ) : null}
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {!selected && (
              <div className="flex h-[560px] items-center justify-center p-8 text-center text-sm text-muted-foreground">
                Run research or select a report from the list.
              </div>
            )}
            {selected?.markdown && viewMode === "markdown" && (
              <ScrollArea className="h-[560px] p-6">
                <ResearchMarkdown markdown={selected.markdown} />
              </ScrollArea>
            )}
            {selected?.markdown && viewMode === "raw" && (
              <ScrollArea className="h-[560px] p-6">
                <pre className="whitespace-pre-wrap font-mono text-sm text-muted-foreground">
                  {selected.markdown}
                </pre>
              </ScrollArea>
            )}
            {selected?.html && viewMode === "html" && (
              <ResearchReportViewer
                artifactId={selected.id}
                html={selected.html}
                title={selected.topic}
              />
            )}
            {selected && !selected.markdown && !selected.html && (
              <div className="flex h-[560px] items-center justify-center p-8 text-center text-sm text-muted-foreground">
                Report content not available yet.
              </div>
            )}
            {selected && (
              <>
                <Separator />
                <div className="px-6 py-4 text-xs text-muted-foreground">
                  Status: {selected.status}
                  {selected.report?.summary
                    ? ` · ${String(selected.report.summary).slice(0, 120)}…`
                    : ""}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
