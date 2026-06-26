"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchReports,
  generateReport,
  type ReportRow,
} from "@/lib/api";
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

function ReportCard({
  report,
  selected,
  onSelect,
}: {
  report: ReportRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const preview = report.body.split("\n")[0] ?? report.body;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-xl border px-3 py-2.5 text-left transition-colors",
        selected
          ? "border-[var(--border-strong)] bg-muted/30"
          : "border-[var(--border)] hover:bg-muted/20",
      )}
    >
      <p className="text-sm font-medium text-foreground">{report.title}</p>
      <p className="mt-0.5 line-clamp-2 font-mono text-[11px] text-[var(--fg-dim)]">
        {preview}
      </p>
      <p className="mt-1 text-[11px] text-[var(--fg-faint)]">
        {formatRelativeTime(report.generated_at)}
      </p>
    </button>
  );
}

export function ReportsWidget() {
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchReports(30);
      setReports(data);
      setSelectedId((prev) => {
        if (prev && data.some((r) => r.id === prev)) return prev;
        return data[0]?.id ?? null;
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load reports");
      setReports([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void load().then(() => {
      if (cancelled) return;
    });
    const timer = setInterval(() => {
      void load();
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load]);

  async function handleGenerate() {
    setGenerating(true);
    setError(null);
    try {
      const report = await generateReport("morning_levels", "SPY");
      setReports((prev) => [report, ...prev.filter((r) => r.id !== report.id)]);
      setSelectedId(report.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generate failed");
    } finally {
      setGenerating(false);
    }
  }

  const selected = reports.find((r) => r.id === selectedId) ?? null;

  if (loading && reports.length === 0) {
    return <Skeleton className="m-3 h-full min-h-24 rounded-md" />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2">
        <p className="font-mono text-[11px] tracking-[0.04em] text-[var(--fg-faint)] uppercase">
          Reports
        </p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 px-2 text-[11px]"
          disabled={generating}
          onClick={() => void handleGenerate()}
        >
          {generating ? "Generating…" : "Generate now"}
        </Button>
      </div>

      {error && (
        <p className="px-3 py-2 font-mono text-xs text-[var(--c-red)]">{error}</p>
      )}

      {reports.length === 0 ? (
        <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
          No reports yet. Use Generate now or wait for the scheduled morning push.
        </p>
      ) : (
        <div className="grid min-h-0 flex-1 gap-2 overflow-hidden p-2 sm:grid-cols-2">
          <ul className="min-h-0 space-y-2 overflow-auto">
            {reports.map((report) => (
              <li key={report.id}>
                <ReportCard
                  report={report}
                  selected={report.id === selectedId}
                  onSelect={() => setSelectedId(report.id)}
                />
              </li>
            ))}
          </ul>
          <div className="min-h-0 overflow-auto rounded-xl border border-[var(--border)] bg-muted/10 p-3">
            {selected ? (
              <>
                <p className="text-sm font-medium text-foreground">
                  {selected.title}
                </p>
                <p className="mt-1 text-[11px] text-[var(--fg-faint)]">
                  {formatRelativeTime(selected.generated_at)}
                </p>
                <pre className="mt-3 whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-[var(--fg-dim)]">
                  {selected.body}
                </pre>
              </>
            ) : (
              <p className="font-mono text-xs text-[var(--fg-dim)]">
                Select a report
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
