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
import { fetchAttention, fetchAbnormalityScanner, type AttentionResult, type AttentionRow, type AbnormalityScannerResult } from "@/lib/api";
import {
  fetchScanPresets,
  runScan,
  type ScanPreset,
  type ScanResultRow,
  type ScanRunResult,
  type ScanUniverse,
} from "@/lib/scans-api";
import { usePageActive } from "@/lib/workspace/page-active-context";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 25_000;

// The volume preset needs per-bar volume, which the S&P 500 store lacks — so it
// is greyed out on that universe (only the live Watchlist path carries volume).
const VOLUME_ONLY_PRESET = "unusual_volume";

// Market-wide pseudo-presets ride the same dropdown (the rvol-scanner widget
// was absorbed 2026-08-09; its list retired into the attention join 2026-08-25
// per ATTENTION_JOIN_DESIGN §4.3). Their universes are their own, so the
// Watchlist/S&P toggle doesn't apply while one is selected.
const ATTENTION_PRESET = "__attention__";
const ABNORM_PRESET = "__abnorm__";

function rvolColor(value: number): string {
  if (value >= 3) return "var(--c-red)";
  if (value >= 2) return "var(--c-amber)";
  if (value >= 1.5) return "var(--c-yellow)";
  return "var(--fg-dim)";
}

function formatUsd(v: number): string {
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${Math.round(v)}`;
}

function formatLitTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
    });
  } catch {
    return "—";
  }
}

// The flow cell: rare-print count + the largest single print, tooltip carries
// min p and the call/put premium split.
function flowCell(row: AttentionRow): { text: string; title: string } | null {
  if (!row.flow_lit) return null;
  const maxPrint = row.flow_max_print_usd != null ? formatUsd(row.flow_max_print_usd) : "—";
  const call = row.flow_call_prem ?? 0;
  const put = row.flow_put_prem ?? 0;
  return {
    text: `${row.flow_events}·${maxPrint}`,
    title:
      `${row.flow_events} rare print${row.flow_events === 1 ? "" : "s"} · largest ${maxPrint}` +
      (row.flow_min_p != null ? ` · min p ${row.flow_min_p.toFixed(3)}` : "") +
      ` · calls ${formatUsd(call)} / puts ${formatUsd(put)}` +
      (row.flow_short_dated_share != null
        ? ` · ${(row.flow_short_dated_share * 100).toFixed(0)}% ≤30 DTE`
        : ""),
  };
}

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
  const [attData, setAttData] = useState<AttentionResult | null>(null);
  const [attBoth, setAttBoth] = useState(false);
  const [attAllQ, setAttAllQ] = useState(false);
  const [abnormData, setAbnormData] = useState<AbnormalityScannerResult | null>(null);
  const [abnormFilter, setAbnormFilter] = useState<"all" | "fired" | "paired" | "base_low" | "base_high">("fired");
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

  const runAttention = useCallback(async () => {
    setRunning(true);
    try {
      const result = await fetchAttention({
        min_quintile: attAllQ ? 0 : 3,
        min_angles: attBoth ? 2 : 1,
        limit: 100,
      });
      if (cancelledRef.current) return;
      setAttData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Attention list failed");
    } finally {
      if (!cancelledRef.current) setRunning(false);
    }
  }, [attBoth, attAllQ]);

  const runAbnorm = useCallback(async () => {
    setRunning(true);
    try {
      const result = await fetchAbnormalityScanner({
        fired: abnormFilter === "fired" || abnormFilter === "paired",
        paired: abnormFilter === "paired",
        base_low: abnormFilter === "base_low",
        base_high: abnormFilter === "base_high",
        limit: 50,
      });
      if (cancelledRef.current) return;
      setAbnormData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Scanner failed");
    } finally {
      if (!cancelledRef.current) setRunning(false);
    }
  }, [abnormFilter]);

  useEffect(() => {
    if (selected !== ABNORM_PRESET) return;
    void runAbnorm();
  }, [abnormFilter, selected, runAbnorm]);

  useEffect(() => {
    if (selected !== ATTENTION_PRESET) return;
    void runAttention();
  }, [attBoth, attAllQ, selected, runAttention]);

  const run = useCallback(
    async (presetId: string) => {
      if (!presetId) return;
      if (presetId === ATTENTION_PRESET) {
        await runAttention();
        return;
      }
      if (presetId === ABNORM_PRESET) {
        await runAbnorm();
        return;
      }
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
    [universe, runAttention, runAbnorm],
  );

  // Keep an already-run scan fresh (only while it matches the selected preset).
  // Gated on the page rather than usePagePoll: a scan is expensive and this poll
  // deliberately has no immediate run — `data` already holds a fresh result.
  const pageActive = usePageActive();
  useEffect(() => {
    if (!pageActive) return;
    const fresh =
      selected === ATTENTION_PRESET
        ? attData !== null
        : selected === ABNORM_PRESET
          ? abnormData !== null
          : data !== null && data.preset_id === selected;
    if (!fresh) return;
    const timer = setInterval(() => void run(selected), POLL_MS);
    return () => clearInterval(timer);
  }, [pageActive, data, attData, abnormData, selected, run]);

  const handlePresetChange = (id: string) => {
    setSelected(id);
    setData(null);
    setError(null);
    // Both market-wide presets are cheap precomputed reads that run on select
    // (attention via its filter effect keyed on `selected`) — no Run press
    // needed for a list that's already there.
    if (id === ABNORM_PRESET) void runAbnorm();
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

  const isAttention = selected === ATTENTION_PRESET;
  const isAbnorm = selected === ABNORM_PRESET;
  const isMarketScan = isAttention || isAbnorm;
  const showResults = !isMarketScan && data && data.preset_id === selected;
  const metricLabel =
    presets.find((p) => p.id === selected)?.metric_label ?? "Value";

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[length:var(--fs-title)]">
      {/* Controls */}
      <div className="flex items-center gap-1.5 border-b border-[var(--line-divider)] px-2 py-1.5">
        {/* Universe toggle: Watchlist ⇄ S&P 500 */}
        <div className="inline-flex shrink-0 overflow-hidden rounded border border-[var(--border)]">
          {UNIVERSES.map((u) => (
            <button
              key={u.id}
              type="button"
              onClick={() => handleUniverseChange(u.id)}
              title={isMarketScan ? "This scan uses its own market-wide universe" : u.title}
              disabled={isMarketScan}
              aria-pressed={!isMarketScan && universe === u.id}
              className={cn(
                "px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
                isMarketScan
                  ? "text-[var(--fg-faint)] opacity-40"
                  : universe === u.id
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
          <option
            value={ATTENTION_PRESET}
            title="Names lit today by either registered angle — unusual range vs own norm, or a rare-sized option print. Angles, not a score; attention, not a forecast."
          >
            Unusual today — attention
          </option>
          <option
            value={ABNORM_PRESET}
            title="Range × volume abnormality vs own time-of-day norm. Magnitude tell, not a direction."
          >
            Abnormality — market-wide
          </option>
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
        {isAttention && attData ? (
          <span className="tabular-nums text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            {attData.count} lit · {formatAsOf(attData.as_of)}
          </span>
        ) : null}
        {isAbnorm && abnormData ? (
          <span className="tabular-nums text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            {abnormData.fires_today ?? 0} fires · {formatAsOf(abnormData.as_of)}
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
      {isAbnorm ? (
        error && !abnormData ? (
          <WidgetError message={error} />
        ) : !abnormData ? (
          <WidgetLoading cols={[2, 1, 1, 1]} />
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-1 border-b border-[var(--line-divider)] px-2 py-1">
              {(
                [
                  ["fired", "fires"],
                  ["paired", "paired"],
                  ["base_low", "base low"],
                  ["base_high", "base high"],
                  ["all", "all"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => {
                    setAbnormFilter(id);
                    setAbnormData(null);
                  }}
                  aria-pressed={abnormFilter === id}
                  className={cn(
                    "px-1 py-0.5 font-mono text-[length:var(--fs-table)]",
                    abnormFilter === id
                      ? "text-[var(--c-accent)]"
                      : "text-[var(--fg-faint)] hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            {abnormData.caveat ? (
              <p
                className="px-2 py-1 font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]"
                title={abnormData.caveat}
              >
                magnitude tell · direction coin flip
                {abnormData.stats?.median_excess != null
                  ? ` · median excess ${abnormData.stats.median_excess.toFixed(2)}×`
                  : ""}
                {abnormData.stats?.direction?.up_wilson_95
                  ? ` · up ${(abnormData.stats.direction.up_wilson_95[0] * 100).toFixed(0)}–${(abnormData.stats.direction.up_wilson_95[1] * 100).toFixed(0)}% Wilson`
                  : ""}
              </p>
            ) : null}
            {abnormData.count === 0 ? (
              <WidgetEmpty message="No names match this filter right now." />
            ) : (
              <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
                <div className="grid grid-cols-[1.25rem_minmax(0,1fr)_3.25rem_3.25rem_4rem] items-center gap-x-1 border-b border-[var(--line-divider)] px-2 py-1.5">
                  <span className="text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.08em] text-[var(--fg-faint)]">
                    #
                  </span>
                  {(["Sym", "Range", "Vol", "Base"] as const).map((h, idx) => (
                    <span
                      key={h}
                      className={cn(
                        "text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.08em] text-[var(--fg-faint)]",
                        idx === 0 ? "text-left" : "text-right",
                      )}
                    >
                      {h}
                    </span>
                  ))}
                </div>
                {abnormData.results.map((row, idx) => (
                  <div
                    key={row.symbol}
                    className="group/row grid grid-cols-[1.25rem_minmax(0,1fr)_3.25rem_3.25rem_4rem] items-center gap-x-1 border-b border-[var(--line-divider)] px-2 py-1.5 leading-[20px] transition-colors hover:bg-[var(--bg-hover)]"
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
                    <span
                      className="text-right tabular-nums"
                      style={{
                        color: rvolColor(row.range_ratio ?? 0),
                      }}
                      title={
                        row.range_pctile != null
                          ? `${(row.range_ratio ?? 0).toFixed(2)}× range · top ${(100 - row.range_pctile).toFixed(1)}% of peers`
                          : undefined
                      }
                    >
                      {row.range_ratio != null ? `${row.range_ratio.toFixed(1)}×` : "—"}
                    </span>
                    <span className="text-right tabular-nums text-[var(--fg-dim)]">
                      {row.vol_ratio != null ? `${row.vol_ratio.toFixed(1)}×` : "—"}
                    </span>
                    <span className="text-right text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                      {row.base_state && row.base_state !== "none"
                        ? row.base_state.replace("base_", "")
                        : "—"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      ) : isAttention ? (
        error && !attData ? (
          <WidgetError message={error} />
        ) : !attData ? (
          <WidgetLoading cols={[1, 3, 1, 1, 2, 1, 1]} />
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-1 border-b border-[var(--line-divider)] px-2 py-1">
              {(
                [
                  [false, "any angle"],
                  [true, "both angles"],
                ] as const
              ).map(([both, label]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => {
                    setAttBoth(both);
                    setAttData(null);
                  }}
                  aria-pressed={attBoth === both}
                  className={cn(
                    "px-1 py-0.5 font-mono text-[length:var(--fs-table)]",
                    attBoth === both
                      ? "text-[var(--c-accent)]"
                      : "text-[var(--fg-faint)] hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
              <span className="text-[var(--fg-faint)]">·</span>
              {(
                [
                  [false, "Q≥3"],
                  [true, "all liquidity"],
                ] as const
              ).map(([allQ, label]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => {
                    setAttAllQ(allQ);
                    setAttData(null);
                  }}
                  aria-pressed={attAllQ === allQ}
                  className={cn(
                    "px-1 py-0.5 font-mono text-[length:var(--fs-table)]",
                    attAllQ === allQ
                      ? "text-[var(--c-accent)]"
                      : "text-[var(--fg-faint)] hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <p
              className="px-2 py-1 font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]"
              title={attData.caveat}
            >
              angles, not a score · attention, not a forecast
              {(attData.stats ?? [])
                .filter((s) => s.median_excess != null)
                .map(
                  (s) =>
                    ` · ${s.angles} angle${s.angles === 1 ? "" : "s"}: ${s.median_excess!.toFixed(2)}× (n=${s.n})`,
                )
                .join("")}
            </p>
            {attData.count === 0 ? (
              <WidgetEmpty message="Nothing lit yet this session." />
            ) : (
              <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
                <div className="grid grid-cols-[1.25rem_minmax(0,1fr)_3.25rem_3.25rem_4.5rem_2rem_2.75rem] items-center gap-x-1 border-b border-[var(--line-divider)] px-2 py-1.5">
                  <span className="text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.08em] text-[var(--fg-table-header)]">
                    #
                  </span>
                  {(["Sym", "Range", "Vol", "Flow", "Q", "Lit"] as const).map((h, idx) => (
                    <span
                      key={h}
                      className={cn(
                        "text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.08em] text-[var(--fg-table-header)]",
                        idx === 0 ? "text-left" : "text-right",
                      )}
                    >
                      {h}
                    </span>
                  ))}
                </div>
                {attData.results.map((row, idx) => {
                  const flow = flowCell(row);
                  return (
                    <div
                      key={row.symbol}
                      className="group/row grid grid-cols-[1.25rem_minmax(0,1fr)_3.25rem_3.25rem_4.5rem_2rem_2.75rem] items-center gap-x-1 border-b border-[var(--line-divider)] px-2 py-1.5 leading-[20px] transition-colors hover:bg-[var(--bg-hover)]"
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
                      <span
                        className="text-right tabular-nums"
                        style={{ color: row.abn_lit ? rvolColor(row.abn_range_ratio ?? 0) : "var(--fg-faint)" }}
                        title={
                          row.abn_lit && row.abn_range_pctile != null
                            ? `range ${(row.abn_range_ratio ?? 0).toFixed(2)}× own norm · top ${(100 - row.abn_range_pctile).toFixed(1)}% of peers${row.abn_repeats ? ` · ${row.abn_repeats} repeat${row.abn_repeats === 1 ? "" : "s"}` : ""}`
                            : undefined
                        }
                      >
                        {row.abn_lit && row.abn_range_ratio != null
                          ? `${row.abn_range_ratio.toFixed(1)}×`
                          : "—"}
                      </span>
                      <span className="text-right tabular-nums text-[var(--fg-dim)]">
                        {row.abn_lit && row.abn_vol_ratio != null
                          ? `${row.abn_vol_ratio.toFixed(1)}×`
                          : "—"}
                      </span>
                      <span
                        className="text-right tabular-nums text-[var(--fg-dim)]"
                        title={flow?.title}
                      >
                        {flow ? flow.text : "—"}
                      </span>
                      <span className="text-right tabular-nums text-[var(--fg-faint)]">
                        {row.quintile != null ? `Q${row.quintile}` : "—"}
                      </span>
                      <span className="text-right tabular-nums text-[var(--fg-faint)]">
                        {formatLitTime(row.first_lit_ts)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )
      ) : !showResults ? (
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
          <div className="grid grid-cols-[1.25rem_minmax(0,1fr)_4rem_3.75rem_3.5rem] items-center gap-x-1 border-b border-[var(--line-divider)] px-2 py-1.5">
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
              className="group/row grid grid-cols-[1.25rem_minmax(0,1fr)_4rem_3.75rem_3.5rem] items-center gap-x-1 border-b border-[var(--line-divider)] px-2 py-1.5 leading-[20px] transition-colors hover:bg-[var(--bg-hover)]"
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
