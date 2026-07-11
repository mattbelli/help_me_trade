"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { TerminalButton } from "@/components/ui/terminal/terminal-button";
import { TerminalSelect } from "@/components/ui/terminal/terminal-select";
import {
  fetchScanPresets,
  runScan,
  type ScanPreset,
  type ScanResultRow,
  type ScanRunResult,
  type ScanUniverse,
} from "@/lib/scans-api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 25_000;

// The volume preset needs per-bar volume, which the S&P 500 store lacks — so it
// is greyed out on that universe (only the live Watchlist path carries volume).
const VOLUME_ONLY_PRESET = "unusual_volume";

const UNIVERSES: ReadonlyArray<{ id: ScanUniverse; label: string; title: string }> = [
  { id: "watchlist", label: "Watchlist", title: "Your watchlist ∪ open positions (live)" },
  { id: "sp500", label: "S&P 500", title: "S&P 500 — screened from stored daily bars" },
];

function formatPrice(v: number | null): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatSigned(v: number | null): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

function formatNum(v: number): string {
  return v.toFixed(2);
}

function formatAsOf(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    });
  } catch {
    return "—";
  }
}

function changeColor(v: number | null): string {
  if (v == null || v === 0) return "var(--fg-dim)";
  return v > 0 ? "var(--c-green)" : "var(--c-red)";
}

export function ScansWidget({ config }: WidgetProps) {
  const [presets, setPresets] = useState<ScanPreset[]>([]);
  const [selected, setSelected] = useState<string>("");
  // Default to the broad S&P 500 scan — the founder wants the bigger screen.
  const [universe, setUniverse] = useState<ScanUniverse>("sp500");
  const [data, setData] = useState<ScanRunResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  // Load the preset list once.
  useEffect(() => {
    let cancelled = false;
    fetchScanPresets()
      .then((items) => {
        if (cancelled) return;
        setPresets(items);
        setSelected((prev) => prev || items[0]?.id || "");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load presets");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const run = useCallback(
    async (presetId: string) => {
      if (!presetId) return;
      setRunning(true);
      try {
        const result = await runScan(presetId, universe, 50);
        if (cancelledRef.current) return;
        setData(result);
        setError(null);
      } catch (err) {
        if (cancelledRef.current) return;
        setError(err instanceof Error ? err.message : "Scan failed");
      } finally {
        if (!cancelledRef.current) setRunning(false);
      }
    },
    [universe],
  );

  // Keep an already-run scan fresh (only while it matches the selected preset).
  useEffect(() => {
    if (!data || data.preset_id !== selected) return;
    const timer = setInterval(() => void run(selected), POLL_MS);
    return () => clearInterval(timer);
  }, [data, selected, run]);

  const handlePresetChange = (id: string) => {
    setSelected(id);
    setData(null);
    setError(null);
  };

  const handleUniverseChange = (next: ScanUniverse) => {
    if (next === universe) return;
    setUniverse(next);
    setData(null);
    setError(null);
    // The volume preset is disabled on S&P 500 — fall back to another preset.
    if (next === "sp500" && selected === VOLUME_ONLY_PRESET) {
      const fallback = presets.find((p) => p.id !== VOLUME_ONLY_PRESET);
      if (fallback) setSelected(fallback.id);
    }
  };

  if (presets.length === 0 && error) {
    return <WidgetError message={error} />;
  }

  if (presets.length === 0) {
    return <WidgetLoading rows={3} cols={[2, 1, 1]} />;
  }

  const showResults = data && data.preset_id === selected;
  const metricLabel =
    presets.find((p) => p.id === selected)?.metric_label ?? "Value";

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[length:var(--fs-title)]">
      {/* Controls */}
      <div className="flex items-center gap-1.5 border-b border-[var(--border)] px-2 py-1.5">
        {/* Universe toggle: Watchlist ⇄ S&P 500 */}
        <div className="inline-flex shrink-0 overflow-hidden rounded border border-[var(--border)]">
          {UNIVERSES.map((u) => (
            <button
              key={u.id}
              type="button"
              onClick={() => handleUniverseChange(u.id)}
              title={u.title}
              aria-pressed={universe === u.id}
              className={cn(
                "px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
                universe === u.id
                  ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                  : "text-[var(--fg-faint)] hover:bg-[var(--bg-hover)] hover:text-foreground",
              )}
            >
              {u.label}
            </button>
          ))}
        </div>
        <TerminalSelect
          value={selected}
          onChange={(e) => handlePresetChange(e.target.value)}
          className="flex-1"
          aria-label="Scan preset"
        >
          {presets.map((p) => {
            const disabled =
              universe === "sp500" && p.id === VOLUME_ONLY_PRESET;
            return (
              <option
                key={p.id}
                value={p.id}
                disabled={disabled}
                title={
                  disabled ? "needs volume — switch to Watchlist" : p.description
                }
              >
                {p.label}
                {disabled ? " (Watchlist only)" : ""}
              </option>
            );
          })}
        </TerminalSelect>
        <TerminalButton
          variant="primary"
          onClick={() => void run(selected)}
          disabled={running || !selected}
        >
          {running ? "…" : "Run"}
        </TerminalButton>
        {showResults ? (
          <span className="tabular-nums text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            {data.universe_size} · {formatAsOf(data.as_of)}
          </span>
        ) : null}
      </div>

      {showResults && data.note ? (
        <p className="px-2 py-1 font-mono text-[length:var(--fs-table)] text-[var(--c-amber)]">
          {data.note}
        </p>
      ) : null}

      {error && showResults ? (
        <p className="px-2 py-1 font-mono text-[length:var(--fs-table)] text-[var(--c-red)]">
          {error}
        </p>
      ) : null}

      {/* Body */}
      {!showResults ? (
        error ? (
          <WidgetError message={error} />
        ) : (
          <WidgetEmpty message="Pick a scan and Run." />
        )
      ) : data.count === 0 ? (
        <WidgetEmpty
          message={`No matches across ${data.universe_size} screened symbols.`}
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
          {/* Column headers */}
          <div className="grid grid-cols-[1.25rem_minmax(0,1fr)_4rem_3.75rem_3.5rem] items-center gap-x-1 border-b border-[var(--border)] px-2 py-1.5">
            <span className="text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.08em] text-[var(--fg-faint)]">
              #
            </span>
            {(["Sym", metricLabel, "Last", "1d %"] as const).map((h, idx) => (
              <span
                key={h}
                className={cn(
                  "truncate text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.08em] text-[var(--fg-faint)]",
                  idx === 0 ? "text-left" : "text-right",
                )}
              >
                {h}
              </span>
            ))}
          </div>

          {/* Rows */}
          {data.results.map((row: ScanResultRow, idx) => (
            <div
              key={row.symbol}
              className="group/row grid grid-cols-[1.25rem_minmax(0,1fr)_4rem_3.75rem_3.5rem] items-center gap-x-1 border-b border-[var(--border)] px-2 py-1.5 transition-colors hover:bg-[var(--bg-hover)]"
            >
              <span className="text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
                {idx + 1}
              </span>
              <span className="truncate font-medium">
                <ClickableSymbol
                  symbol={row.symbol}
                  linked={linked}
                  onActivate={() => publish(row.symbol)}
                />
              </span>
              <span className="text-right tabular-nums text-foreground">
                {formatNum(row.value)}
              </span>
              <span className="text-right tabular-nums text-[var(--fg-dim)]">
                {formatPrice(row.price)}
              </span>
              <span
                className="text-right tabular-nums"
                style={{ color: changeColor(row.change_pct) }}
              >
                {formatSigned(row.change_pct)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
