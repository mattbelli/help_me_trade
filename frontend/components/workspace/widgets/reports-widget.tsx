"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { TerminalButton, TerminalSelect } from "@/components/ui/terminal";
import {
  fetchReports,
  generateReport,
  REPORT_KINDS,
  type GenerateKind,
  type ReportRow,
} from "@/lib/reports-api";
import {
  captureThemeVars,
  THEME_VARS_EVENT,
} from "@/lib/workspace/custom-themes";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

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

function kindLabel(kind: string): string {
  return kind.replace(/_/g, " ");
}

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
      <p className="truncate font-mono text-[11px] text-foreground">
        {report.title}
      </p>
      <p className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-[var(--fg-faint)]">
        <span className="rounded-sm border border-[var(--border)] px-1 text-[var(--fg-dim)] uppercase">
          {kindLabel(report.kind)}
        </span>
        <span>{formatRelativeTime(report.generated_at)}</span>
      </p>
    </button>
  );
}

/** Resolve a color-mix (or other) expression to a concrete computed color. */
function resolveComputedColor(expression: string): string {
  if (typeof document === "undefined") return expression;
  const el = document.createElement("div");
  el.style.position = "absolute";
  el.style.visibility = "hidden";
  el.style.backgroundColor = expression;
  document.body.appendChild(el);
  const resolved = getComputedStyle(el).backgroundColor;
  document.body.removeChild(el);
  return resolved || expression;
}

function buildReportThemeOverride(): Record<string, string> {
  const app = captureThemeVars();
  const card = app["--card"] ?? "";
  const bgInset = app["--bg-inset"] ?? "";
  const bgRaised = app["--bg-raised"] ?? "";
  const cAmber = app["--c-amber"] ?? "";
  const foreground = app["--foreground"] ?? "";

  let warnBg = bgInset;
  if (cAmber && card) {
    warnBg = resolveComputedColor(
      `color-mix(in srgb, ${cAmber} 12%, ${card})`,
    );
  }

  return {
    "--bg": app["--background"] ?? "",
    "--card": card,
    "--text": foreground,
    "--muted": app["--muted-foreground"] ?? "",
    "--accent": app["--c-accent"] ?? "",
    "--border": app["--border"] ?? "",
    "--hero": bgRaised,
    "--badge-bg": bgInset || bgRaised,
    "--summary-text": app["--fg-dim"] ?? "",
    "--warn-bg": warnBg,
    "--warn-border": cAmber || bgInset,
    "--warn-text": foreground,
    "--warn-strong": foreground,
  };
}

function injectReportTheme(body: string, vars: Record<string, string>): string {
  const decl = Object.entries(vars)
    .filter(([, v]) => v)
    .map(([name, value]) => `${name}:${value}`)
    .join(";");
  if (!decl) return body;
  const style = `<style>:root{${decl}}</style>`;
  const headClose = body.indexOf("</head>");
  if (headClose !== -1) {
    return `${body.slice(0, headClose)}${style}${body.slice(headClose)}`;
  }
  return `${style}${body}`;
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
    <div className="h-full overflow-auto p-3">
      <p className="font-mono text-[11px] text-foreground">{report.title}</p>
      <p className="mt-1 font-mono text-[10px] text-[var(--fg-faint)]">
        {formatRelativeTime(report.generated_at)}
      </p>
      <pre className="mt-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-[var(--fg-dim)]">
        {report.body}
      </pre>
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

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

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
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2">
        <p className="font-mono text-[11px] tracking-[0.04em] text-[var(--fg-faint)] uppercase">
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
        <p className="border-b border-[var(--border)] px-3 py-2 font-mono text-[11px] text-[var(--c-red)]">
          {error}
        </p>
      )}

      {loading && reports.length === 0 ? (
        <p className="p-3 font-mono text-[11px] text-[var(--fg-dim)]">Loading…</p>
      ) : reports.length === 0 ? (
        <p className="p-3 font-mono text-[11px] text-[var(--fg-dim)]">
          No reports yet. Use Generate or wait for a scheduled brief.
        </p>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(9rem,14rem)_1fr] overflow-hidden">
          <ul className="min-h-0 divide-y divide-[var(--border)] overflow-auto border-r border-[var(--border)]">
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
              <p className="p-3 font-mono text-[11px] text-[var(--fg-dim)]">
                Select a report
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
