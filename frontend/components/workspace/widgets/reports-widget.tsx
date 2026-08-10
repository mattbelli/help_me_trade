"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ResearchMarkdown } from "@/components/research/research-markdown";
import { TerminalButton, TerminalSelect } from "@/components/ui/terminal";
import {
  WidgetEmpty,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  fetchReports,
  generateReport,
  REPORT_KINDS,
  type GenerateKind,
  type ReportRow,
} from "@/lib/reports-api";
import {
  buildReportThemeOverride,
  injectReportTheme,
} from "@/lib/report-theme";
import { THEME_VARS_EVENT } from "@/lib/workspace/custom-themes";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import { label } from "@/lib/labels";
import { relativeTime } from "@/lib/relative-time";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

function ReportListItem({
  report,
  selected,
  onSelect,
}: {
  report: ReportRow;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full border-l-2 px-2.5 py-2 text-left transition-colors",
        selected
          ? "border-l-[var(--c-accent)] bg-[var(--bg-hover)]"
          : "border-l-transparent hover:bg-[var(--bg-hover)]",
      )}
    >
      <p className="truncate font-mono text-[length:var(--fs-body)] text-foreground">
        {report.title}
      </p>
      <p className="mt-0.5 flex items-center gap-1.5 font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
        <span className="rounded-sm border border-[var(--border)] px-1 text-[var(--fg-dim)] uppercase">
          {label("report_kind", report.kind)}
        </span>
        <span>{relativeTime(report.generated_at)}</span>
      </p>
    </button>
  );
}

function ReportViewer({ report }: { report: ReportRow }) {
  const [themeNonce, setThemeNonce] = useState(0);

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
    if (report.format !== "html") return report.body;
    if (typeof document === "undefined") return report.body;
    return injectReportTheme(report.body, buildReportThemeOverride());
  }, [report.body, report.format, themeNonce]);

  if (report.format === "html") {
    return (
      <iframe
        title={report.title}
        srcDoc={themedSrcDoc}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        className="h-full w-full border-0"
      />
    );
  }
  return (
    <div className="h-full overflow-auto p-3 scroll-shadow-y">
      <p className="font-mono text-[length:var(--fs-body)] text-foreground">{report.title}</p>
      <p className="mt-1 font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
        {relativeTime(report.generated_at)}
      </p>
      {report.format === "markdown" ? (
        <div className="mt-3">
          <ResearchMarkdown markdown={report.body} />
        </div>
      ) : (
        <pre className="mt-3 font-mono text-[length:var(--fs-title)] leading-relaxed whitespace-pre-wrap text-[var(--fg-dim)]">
          {report.body}
        </pre>
      )}
    </div>
  );
}

export function ReportsWidget() {
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [genKind, setGenKind] = useState<GenerateKind>("levels");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchReports(30);
      setReports(data);
      // Default to the most recent (top) report so a report_ready click lands here.
      setSelectedId((prev) =>
        prev && data.some((r) => r.id === prev) ? prev : (data[0]?.id ?? null),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load reports");
      setReports([]);
    } finally {
      setLoading(false);
    }
  }, []);

  usePagePoll(load, POLL_MS);

  async function handleGenerate() {
    setGenerating(true);
    setError(null);
    try {
      const symbols = genKind === "levels" ? ["SPY"] : undefined;
      const report = await generateReport({ kind: genKind, symbols });
      setReports((prev) => [report, ...prev.filter((r) => r.id !== report.id)]);
      setSelectedId(report.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generate failed");
    } finally {
      setGenerating(false);
    }
  }

  const selected = useMemo(
    () => reports.find((r) => r.id === selectedId) ?? null,
    [reports, selectedId],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--line-divider)] px-3 py-2">
        <p className="font-mono text-[length:var(--fs-body)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
          Reports
        </p>
        <div className="flex items-center gap-1.5">
          <TerminalSelect
            value={genKind}
            onChange={(e) => setGenKind(e.target.value as GenerateKind)}
            disabled={generating}
            className="w-28"
            aria-label="Report kind"
          >
            {REPORT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </TerminalSelect>
          <TerminalButton
            variant="primary"
            disabled={generating}
            onClick={() => void handleGenerate()}
          >
            {generating ? "Generating…" : "Generate"}
          </TerminalButton>
        </div>
      </div>

      {error && (
        <p className="border-b border-[var(--line-divider)] px-3 py-2 font-mono text-[length:var(--fs-body)] text-[var(--c-red)]">
          {error}
        </p>
      )}

      {loading && reports.length === 0 ? (
        <WidgetLoading rows={4} cols={[1]} />
      ) : reports.length === 0 ? (
        <WidgetEmpty message="No reports yet. Use Generate or wait for a scheduled brief." />
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(9rem,14rem)_1fr] overflow-hidden">
          <ul className="min-h-0 divide-y divide-[var(--line-divider)] overflow-auto border-r border-[var(--line-divider)] scroll-shadow-y">
            {reports.map((report) => (
              <li key={report.id}>
                <ReportListItem
                  report={report}
                  selected={report.id === selectedId}
                  onSelect={() => setSelectedId(report.id)}
                />
              </li>
            ))}
          </ul>
          <div className="min-h-0 overflow-hidden bg-[var(--bg-inset)]">
            {selected ? (
              <ReportViewer report={selected} />
            ) : (
              <WidgetEmpty message="Select a report" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
