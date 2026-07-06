"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, ExternalLink, FileText, Trash2 } from "lucide-react";

import { downloadResearchMarkdown } from "@/components/research/research-download";
import { ResearchMarkdown } from "@/components/research/research-markdown";
import { Button } from "@/components/ui/button";
import { FloatingPanel } from "@/components/ui/floating-panel";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { apiFetch } from "@/lib/api";
import {
  buildReportThemeOverride,
  injectReportTheme,
} from "@/lib/report-theme";
import { researchHtmlUrl, type ResearchArtifact } from "@/lib/research";
import { THEME_VARS_EVENT } from "@/lib/workspace/custom-themes";
import { cn } from "@/lib/utils";

export type ResearchViewMode = "markdown" | "html" | "raw";

export const RESEARCH_DELETE_CONFIRM =
  "Delete this research report? This cannot be undone.";

export function useResearchViewMode(
  artifact: ResearchArtifact | null,
  selectionKey: number | null,
) {
  const [viewMode, setViewMode] = useState<ResearchViewMode>("markdown");

  useEffect(() => {
    setViewMode("markdown");
  }, [selectionKey]);

  useEffect(() => {
    if (!artifact) return;
    if (!artifact.markdown?.trim() && artifact.html) {
      setViewMode("html");
    }
  }, [artifact?.id, artifact?.markdown, artifact?.html]);

  return [viewMode, setViewMode] as const;
}

export function ResearchReportViewer({
  artifactId,
  html,
  title,
  className,
}: {
  artifactId: number;
  html: string | null;
  title: string;
  className?: string;
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
      <div
        className={cn(
          "flex items-center justify-center text-sm text-muted-foreground",
          className ?? "h-[560px]",
        )}
      >
        Loading report…
      </div>
    );
  }

  return (
    <iframe
      title={title}
      srcDoc={themedSrcDoc}
      className={cn("w-full border-0 bg-[var(--bg-inset)]", className ?? "h-[560px]")}
      sandbox="allow-same-origin"
    />
  );
}

export function ResearchViewerActionBar({
  artifact,
  viewMode,
  onViewModeChange,
  onDelete,
  className,
}: {
  artifact: ResearchArtifact | null;
  viewMode: ResearchViewMode;
  onViewModeChange: (mode: ResearchViewMode) => void;
  onDelete?: (id: number) => void;
  className?: string;
}) {
  const [fullReportOpen, setFullReportOpen] = useState(false);

  if (!artifact) return null;

  const actionBtnClass =
    "h-6 gap-1 px-2 py-1 font-mono text-[length:var(--fs-body)] [&_svg:not([class*='size-'])]:size-3.5";

  return (
    <>
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {artifact.markdown ? (
        <>
          <Button
            type="button"
            variant={viewMode === "markdown" ? "default" : "outline"}
            size="xs"
            className={actionBtnClass}
            onClick={() => onViewModeChange("markdown")}
          >
            <FileText className="size-3.5" />
            Preview
          </Button>
          <Button
            type="button"
            variant={viewMode === "raw" ? "default" : "outline"}
            size="xs"
            className={actionBtnClass}
            onClick={() => onViewModeChange("raw")}
          >
            Raw
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            className={actionBtnClass}
            onClick={() => downloadResearchMarkdown(artifact)}
          >
            <Download className="size-3.5" />
            Download .md
          </Button>
        </>
      ) : null}
      {artifact.html ? (
        <>
          <Button
            type="button"
            variant={viewMode === "html" ? "default" : "outline"}
            size="xs"
            className={actionBtnClass}
            onClick={() => onViewModeChange("html")}
          >
            HTML
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            className={actionBtnClass}
            onClick={() => setFullReportOpen(true)}
          >
            <ExternalLink className="size-3.5" />
            Open full report
          </Button>
        </>
      ) : null}
      {onDelete ? (
        <Button
          type="button"
          variant="outline"
          size="xs"
          className={cn(actionBtnClass, "text-destructive hover:text-destructive")}
          onClick={() => onDelete(artifact.id)}
        >
          <Trash2 className="size-3.5" />
          Delete
        </Button>
      ) : null}
    </div>
    {artifact.html ? (
      <FloatingPanel
        open={fullReportOpen}
        onClose={() => setFullReportOpen(false)}
        seedKey={artifact.id}
        initialWidth={900}
        initialHeight={640}
        ariaLabel={artifact.topic}
        className="w-[min(900px,90vw)] h-[85vh]"
        contentClassName="flex"
        header={
          <div className="flex min-w-0 items-center gap-3">
            <span className="line-clamp-1 min-w-0 flex-1 font-mono text-[length:var(--fs-title)] leading-snug">
              {artifact.topic}
            </span>
            <a
              href={researchHtmlUrl(artifact.id)}
              target="_blank"
              rel="noopener noreferrer"
              onPointerDown={(e) => e.stopPropagation()}
              className="inline-flex shrink-0 items-center gap-1 font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)] hover:text-foreground"
            >
              open in tab
              <ExternalLink className="size-3.5" />
            </a>
          </div>
        }
      >
        <ResearchReportViewer
          artifactId={artifact.id}
          html={artifact.html}
          title={artifact.topic}
          className="h-full w-full"
        />
      </FloatingPanel>
    ) : null}
    </>
  );
}

export function ResearchViewerBody({
  artifact,
  viewMode,
  bodyClassName = "h-[560px]",
}: {
  artifact: ResearchArtifact | null;
  viewMode: ResearchViewMode;
  bodyClassName?: string;
}) {
  if (!artifact) {
    return (
      <div
        className={cn(
          "flex items-center justify-center p-8 text-center text-sm text-muted-foreground",
          bodyClassName,
        )}
      >
        Run research or select a report from the list.
      </div>
    );
  }

  if (artifact.markdown && viewMode === "markdown") {
    return (
      <ScrollArea className={cn("p-6", bodyClassName)}>
        <ResearchMarkdown markdown={artifact.markdown} />
      </ScrollArea>
    );
  }

  if (artifact.markdown && viewMode === "raw") {
    return (
      <ScrollArea className={cn("p-6", bodyClassName)}>
        <pre className="whitespace-pre-wrap font-mono text-sm text-muted-foreground">
          {artifact.markdown}
        </pre>
      </ScrollArea>
    );
  }

  if (artifact.html && viewMode === "html") {
    return (
      <ResearchReportViewer
        artifactId={artifact.id}
        html={artifact.html}
        title={artifact.topic}
        className={bodyClassName}
      />
    );
  }

  if (!artifact.markdown && !artifact.html) {
    return (
      <div
        className={cn(
          "flex items-center justify-center p-8 text-center text-sm text-muted-foreground",
          bodyClassName,
        )}
      >
        Report content not available yet.
      </div>
    );
  }

  return null;
}

export function ResearchViewerFooter({ artifact }: { artifact: ResearchArtifact }) {
  return (
    <>
      <Separator />
      <div className="px-6 py-4 text-xs text-muted-foreground">
        Status: {artifact.status}
        {artifact.report?.summary
          ? ` · ${String(artifact.report.summary).slice(0, 120)}…`
          : ""}
      </div>
    </>
  );
}
