"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Info } from "lucide-react";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { useTickerInfo } from "@/components/workspace/ticker-info-provider";
import {
  fetchOptionsFlow,
  type OptionsFlowEvent,
  type OptionsFlowResult,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import {
  DEFAULT_FLOW_TYPES,
  type WidgetInstanceConfig,
} from "@/lib/workspace/layout-persistence";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

// 5s: the tape's detection side runs at ~2-4s from the exchange print; the widget
// refresh is the dominant last-hop latency and the founder acts on flow in 1-3min.
// Cheap: one capped local-SQLite query per poll. (True SSE push = notifications redesign.)
const POLL_MS = 5_000;
const EOD_TS_SUFFIX = "T21:00:00Z";

// The whole table lives in a horizontal scroller so a narrow widget keeps the
// columns aligned instead of reflowing. These px widths mirror the mockup.
// score | time | ticker | side | type | strike | exp | dte | size | prem | flag
const GRID_COLS = "44px 46px 58px 66px 78px 56px 48px 30px 54px 66px 58px";
const GRID_MIN = "min-w-[632px]";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const;

type SortKey = "score" | "time" | "size" | "premium";
type SortState = { key: SortKey; dir: "asc" | "desc" };
const DEFAULT_SORT: SortState = { key: "score", dir: "desc" };

type FlowKind = "uv1" | "uv2" | "block" | "sweep" | "pressure" | "burst" | "cluster";

function formatPremium(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function formatStrike(strike: number | null): string {
  if (strike == null) return "—";
  if (strike === Math.trunc(strike)) return `$${strike.toFixed(0)}`;
  return `$${strike}`;
}

function formatExpiry(expiry: string | null): string {
  if (!expiry) return "—";
  const month = Number.parseInt(expiry.slice(5, 7), 10);
  const day = expiry.slice(8, 10);
  if (month >= 1 && month <= 12) return `${MONTHS[month - 1]}${day}`;
  return "—";
}

function formatTimeEt(ts: string): string {
  if (ts.endsWith(EOD_TS_SUFFIX)) return "EOD";
  try {
    // 12-hour, compact a/p suffix so the column stays tight: "1:53p"
    const t = new Date(ts).toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
    return t.replace(" AM", "a").replace(" PM", "p");
  } catch {
    return "—";
  }
}

function rightLetter(right: string | null): "C" | "P" | null {
  if (!right) return null;
  const r = right.trim().toLowerCase();
  if (r.startsWith("c")) return "C";
  if (r.startsWith("p")) return "P";
  return null;
}

function flowKind(ev: OptionsFlowEvent): FlowKind {
  if (ev.event_type === "block") return "block";
  if (ev.event_type === "sweep") return "sweep";
  if (ev.event_type === "flow_burst") return "burst";
  if (ev.event_type === "flow_cluster") return "cluster";
  if (ev.event_type === "directional_pressure") return "pressure";
  if (ev.event_type === "unusual_volume") return ev.occ_symbol ? "uv1" : "uv2";
  return "uv1";
}

// One muted vocabulary — event type never carries its own hue (side owns color).
function typeLabel(kind: FlowKind): string {
  switch (kind) {
    case "block": return "block";
    case "sweep": return "sweep";
    case "burst": return "burst";
    case "cluster": return "cluster";
    case "uv1": return "unusual vol";
    case "uv2": return "chain surge";
    case "pressure": return "pressure";
  }
}

/** SIDE cell — color follows the aggressor side, the ONLY saturated hue. */
function sideCell(ev: OptionsFlowEvent): { label: string; cls: string; tint: string } {
  const letter = rightLetter(ev.right);
  const word = letter === "C" ? "call" : letter === "P" ? "put" : "";
  if (ev.direction === "buy_side")
    return { label: `▲ ${word}`.trim(), cls: "text-[var(--c-green)]", tint: "var(--c-green)" };
  if (ev.direction === "sell_side")
    return { label: `▼ ${word}`.trim(), cls: "text-[var(--c-red)]", tint: "var(--c-red)" };
  return {
    label: word ? `◇ ${word}` : "◇ mixed",
    cls: "text-[var(--fg-faint)]",
    tint: "var(--c-accent)",
  };
}

/**
 * Honesty flag from ``direction_basis`` — always visible, never a tooltip.
 * live quote at ask/bid → "—"; ≤30s polled quote → "polled" (may shift);
 * mid-spread / no quote → the side is undetermined, so "no side" (never guessed).
 */
function honestyFlag(ev: OptionsFlowEvent): { label: string; cls: string } {
  if (ev.direction === "undetermined")
    return { label: "no side", cls: "text-[var(--fg-faint)]" };
  const b = (ev.direction_basis || "").toLowerCase();
  if (b.startsWith("polled"))
    return { label: "polled", cls: "text-[var(--c-yellow)]" };
  return { label: "—", cls: "text-[var(--fg-faint)]" };
}

type ScoreComp = { label: string; pts: number };

const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
// The intraday tape's detector floor is $25k and its whales top out near $5M,
// so size is a log10 map of $25k..$5M → 0..4 points. EOD unusual-volume premiums
// (often $1M+, up to $170M) simply saturate size at 4 — they ARE bigger.
const L_LO = Math.log10(25_000);
const L_HI = Math.log10(5_000_000);

/**
 * v1 heuristic composite — deliberately transparent (components shown on hover +
 * in the drawer), to be replaced by a calibrated score later (board: flow-notif-redesign).
 * Tuned against the real event distribution: size (log premium) + prints (sweep
 * aggression — one order swept across many exchanges) + unusualness (vol z-score,
 * or vol/OI, present on EOD events only — the intraday tape never carries them) +
 * a confirmed-aggressor bump. Real spread: tape ~0.4..6, EOD ~0.3..7.
 */
function computeScore(ev: OptionsFlowEvent): { score: number; comps: ScoreComp[] } {
  const comps: ScoreComp[] = [];
  const prem = ev.premium_usd ?? 0;
  const size = prem > 0 ? clamp(((Math.log10(prem) - L_LO) / (L_HI - L_LO)) * 4, 0, 4) : 0;
  if (size > 0) comps.push({ label: `size ${formatPremium(prem)}`, pts: size });

  // Multi-print sweeps = one aggressor hitting many books at once (p50 6, p90 28).
  const nPrints = ev.n_prints ?? 0;
  const prints = nPrints > 1 ? clamp(Math.log2(nPrints) / 6, 0, 1) * 1.5 : 0;
  if (prints > 0) comps.push({ label: `${nPrints} prints`, pts: prints });

  const z = ev.vol_zscore;
  const voi = ev.vol_oi_ratio ?? 0;
  let unusual = 0;
  if (z != null) {
    unusual = clamp((z / 12) * 3, 0, 3);
    if (unusual > 0) comps.push({ label: `vol z ${z.toFixed(1)}σ`, pts: unusual });
  } else if (voi > 1) {
    unusual = clamp((Math.log2(voi) / 4) * 3, 0, 3);
    if (unusual > 0) comps.push({ label: `vol/OI ${voi.toFixed(1)}×`, pts: unusual });
  }

  const aggr =
    ev.direction !== "undetermined" && ev.direction_basis === "stream_quote" ? 1.5 : 0;
  if (aggr > 0) comps.push({ label: "confirmed aggressor", pts: aggr });

  const score = clamp(size + prints + unusual + aggr, 0, 10);
  return { score, comps };
}

function scoreTitle(score: number, comps: ScoreComp[]): string {
  const lines = comps.map((c) => `  +${c.pts.toFixed(1)}  ${c.label}`);
  return `score ${score.toFixed(1)} — v1 heuristic (not calibrated)\n${lines.join("\n")}`;
}

function num(detail: Record<string, number>, key: string): number | null {
  const v = detail[key];
  return typeof v === "number" ? v : null;
}

type Scored = { ev: OptionsFlowEvent; score: number; comps: ScoreComp[] };

function sortValue(s: Scored, key: SortKey): number {
  switch (key) {
    case "score": return s.score;
    case "size": return s.ev.contracts ?? 0;
    case "premium": return s.ev.premium_usd ?? 0;
    case "time": {
      const t = Date.parse(s.ev.ts);
      return Number.isNaN(t) ? 0 : t;
    }
  }
}

export function OptionsFlowWidget({ config, onConfigChange }: WidgetProps) {
  const [data, setData] = useState<OptionsFlowResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterCleared, setFilterCleared] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const cancelledRef = useRef(false);

  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const { publish, linked } = usePublishSymbol(channel);
  const { openTickerInfo } = useTickerInfo();

  const sort: SortState = config.flowSort ?? DEFAULT_SORT;

  const activeFilter =
    channel !== "none" && channelSymbol && !filterCleared ? channelSymbol : undefined;

  // ⚙ event-type filter (frame header) — the fetch pulls only the checked kinds,
  // so the nightly unusual-volume EOD batch never buries the live sweep/block tape.
  const flowTypes = config.flowTypes ?? DEFAULT_FLOW_TYPES;
  const flowTypesKey = flowTypes.join(",");

  useEffect(() => {
    setFilterCleared(false);
  }, [channelSymbol]);

  const load = useCallback(async () => {
    if (!flowTypesKey) {
      if (cancelledRef.current) return;
      setData({ as_of: "", count: 0, events: [] });
      setError(null);
      setLoading(false);
      return;
    }
    try {
      const result = await fetchOptionsFlow(200, activeFilter, flowTypesKey);
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load options flow");
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, [activeFilter, flowTypesKey]);

  useEffect(() => {
    cancelledRef.current = false;
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [load]);

  const rows = useMemo<Scored[]>(() => {
    const scored = (data?.events ?? []).map((ev) => ({ ev, ...computeScore(ev) }));
    const dir = sort.dir === "asc" ? 1 : -1;
    return scored.sort((a, b) => {
      const d = (sortValue(a, sort.key) - sortValue(b, sort.key)) * dir;
      return d !== 0 ? d : Date.parse(b.ev.ts) - Date.parse(a.ev.ts);
    });
  }, [data, sort.key, sort.dir]);

  const handleSort = useCallback(
    (key: SortKey) => {
      const next: SortState =
        sort.key === key
          ? { key, dir: sort.dir === "asc" ? "desc" : "asc" }
          : { key, dir: "desc" };
      onConfigChange({ flowSort: next as WidgetInstanceConfig["flowSort"] });
    },
    [sort, onConfigChange],
  );

  const toggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  if (loading && !data) return <WidgetLoading cols={[1, 2, 1]} />;
  if (error && !data) return <WidgetError message={error} onRetry={load} />;

  const arrow = (key: SortKey) =>
    sort.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : "";

  const headCls = "text-left text-[var(--fg-table-header)]";
  const sortableCls =
    "cursor-pointer transition-colors hover:text-[var(--c-accent)]";

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[length:var(--fs-title)]">
      {rows.length === 0 ? (
        <WidgetEmpty
          message={
            flowTypesKey
              ? "No matching options flow. Live sweeps & blocks land here during market hours — widen the flow-type filter (⚙) to include more."
              : "No flow types selected. Open ⚙ and pick sweeps, blocks, or unusual volume."
          }
        />
      ) : (
        <div className="relative min-h-0 flex-1 overflow-auto scroll-shadow-y">
          {activeFilter ? (
            <div className="flex items-center gap-1 border-b border-[var(--border)] px-2 py-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
              <span>filtering {activeFilter}</span>
              <span>·</span>
              <button
                type="button"
                aria-label="Clear symbol filter"
                onClick={() => setFilterCleared(true)}
                className="text-[var(--fg-faint)] transition-colors hover:text-[var(--fg-dim)]"
              >
                ×
              </button>
            </div>
          ) : null}

          <div className={cn("relative", GRID_MIN)}>
            {/* column header — sortable like the watchlist */}
            <div
              className="sticky top-0 z-20 grid items-center gap-x-2 border-b border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.08em]"
              style={{ gridTemplateColumns: GRID_COLS }}
            >
              <span className={cn(headCls, sortableCls, sort.key === "score" && "text-[var(--c-accent)]")} onClick={() => handleSort("score")}>score{arrow("score")}</span>
              <span className={cn(headCls, sortableCls, sort.key === "time" && "text-[var(--c-accent)]")} onClick={() => handleSort("time")}>time{arrow("time")}</span>
              <span className={headCls}>ticker</span>
              <span className={headCls}>side</span>
              <span className={headCls}>type</span>
              <span className={headCls}>strike</span>
              <span className={headCls}>exp</span>
              <span className={cn(headCls, "text-right")}>dte</span>
              <span className={cn(headCls, "text-right", sortableCls, sort.key === "size" && "text-[var(--c-accent)]")} onClick={() => handleSort("size")}>size{arrow("size")}</span>
              <span className={cn(headCls, "text-right", sortableCls, sort.key === "premium" && "text-[var(--c-accent)]")} onClick={() => handleSort("premium")}>prem{arrow("premium")}</span>
              <span className={headCls}>flag</span>
            </div>

            {rows.map(({ ev, score, comps }) => {
              const side = sideCell(ev);
              const flag = honestyFlag(ev);
              const kind = flowKind(ev);
              const expanded = expandedIds.has(ev.id);
              const hasDetail = ev.detail != null && Object.keys(ev.detail).length > 0;
              return (
                <div key={ev.id} className="relative border-b border-[var(--border)]">
                  <div
                    role="button"
                    tabIndex={0}
                    aria-expanded={expanded}
                    onClick={() => toggleExpand(ev.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        toggleExpand(ev.id);
                      }
                    }}
                    className="group/row relative grid cursor-pointer items-center gap-x-2 overflow-hidden px-2 py-1.5 text-left transition-colors hover:bg-[var(--bg-hover)]"
                    style={{ gridTemplateColumns: GRID_COLS }}
                  >
                    {/* magnitude bar — width = score, tinted by side; scoped to the
                        row so it never washes over the expanded drawer below */}
                    <div
                      aria-hidden
                      className="pointer-events-none absolute inset-y-0 left-0"
                      style={{
                        width: `${score * 10}%`,
                        background: `linear-gradient(90deg, color-mix(in srgb, ${side.tint} 22%, transparent), transparent)`,
                      }}
                    />
                    <span
                      className="tabular-nums font-medium text-[var(--c-accent)]"
                      title={scoreTitle(score, comps)}
                    >
                      {score.toFixed(1)}
                    </span>
                    <span className="tabular-nums text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                      {formatTimeEt(ev.ts)}
                    </span>
                    <span className="inline-flex min-w-0 items-center gap-1">
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={`Focus ${ev.underlying}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          publish(ev.underlying);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            e.stopPropagation();
                            publish(ev.underlying);
                          }
                        }}
                        className={cn(
                          "truncate font-medium text-[var(--c-accent)]",
                          linked ? "cursor-pointer hover:underline" : "cursor-default",
                        )}
                      >
                        {ev.underlying}
                      </span>
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={`${ev.underlying} info`}
                        onClick={(e) => {
                          e.stopPropagation();
                          openTickerInfo(ev.underlying);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            e.stopPropagation();
                            openTickerInfo(ev.underlying);
                          }
                        }}
                        className="shrink-0 cursor-pointer text-[var(--fg)] opacity-0 transition-opacity group-hover/row:opacity-100 focus-visible:opacity-100"
                      >
                        <Info className="size-3" />
                      </span>
                    </span>
                    <span className={cn("truncate lowercase", side.cls)}>{side.label}</span>
                    <span className="truncate lowercase text-[var(--fg-dim)]">{typeLabel(kind)}</span>
                    <span className="tabular-nums text-[var(--fg-dim)]">
                      {formatStrike(ev.strike)}
                    </span>
                    <span className="tabular-nums text-[var(--fg-dim)]">
                      {formatExpiry(ev.expiry)}
                    </span>
                    <span className="text-right tabular-nums text-[var(--fg-dim)]">
                      {ev.dte != null ? `${ev.dte}d` : "—"}
                    </span>
                    <span className="text-right tabular-nums text-[var(--fg-dim)]">
                      {ev.contracts != null ? ev.contracts.toLocaleString() : "—"}
                    </span>
                    <span className="text-right tabular-nums font-medium text-[var(--fg)]">
                      {formatPremium(ev.premium_usd)}
                    </span>
                    <span className={cn("truncate lowercase text-[length:var(--fs-micro)]", flag.cls)}>
                      {flag.label}
                    </span>
                  </div>

                  {expanded ? (
                    <FlowDetail ev={ev} score={score} comps={comps} hasDetail={hasDetail} />
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/** Plain depth drawer — no card; the widget's deepest view. */
function FlowDetail({
  ev,
  score,
  comps,
  hasDetail,
}: {
  ev: OptionsFlowEvent;
  score: number;
  comps: ScoreComp[];
  hasDetail: boolean;
}) {
  const d = ev.detail ?? {};
  const bid = num(d, "bid");
  const ask = num(d, "ask");
  const iv = num(d, "iv");
  const ivPct = iv != null ? (iv <= 2 ? iv * 100 : iv) : null;
  const delta = num(d, "delta");
  const gamma = num(d, "gamma");
  const theta = num(d, "theta");
  const vega = num(d, "vega");
  const oi = num(d, "oi");
  const spot = num(d, "underlying_price");

  const hitSide =
    ev.direction === "buy_side" ? "hit ask" : ev.direction === "sell_side" ? "hit bid" : null;

  const basis =
    ev.direction === "undetermined"
      ? "mid-spread — no side claimed"
      : ev.direction_basis === "stream_quote"
        ? "live stream quote"
        : (ev.direction_basis || "").toLowerCase().startsWith("polled")
          ? "polled quote (≤30s, may shift)"
          : ev.direction_basis || "—";

  const isAggregate =
    ev.event_type === "flow_burst" || ev.event_type === "flow_cluster";
  const classifiedFrac = num(d, "classified_frac");
  const modalShare = num(d, "modal_size_share");
  const cadenceCv = num(d, "interarrival_cv");

  const why: string[] = [];
  // aggregate events carry their whole story in reason — lead with it
  if (isAggregate && ev.reason) why.push(ev.reason);
  if (ev.direction === "buy_side") why.push("aggressive buy-side");
  else if (ev.direction === "sell_side") why.push("aggressive sell-side");
  if (ev.otm_pct != null) why.push(`${Math.abs(ev.otm_pct).toFixed(1)}% OTM`);
  if (ev.dte != null) why.push(`${ev.dte} DTE`);
  if (ev.direction !== "undetermined") {
    why.push(
      ev.direction_basis === "stream_quote"
        ? "side confirmed on a live quote"
        : "side off a polled quote",
    );
  }

  const cell = (label: string, value: string, title?: string) => (
    <div className="flex flex-col gap-0.5" title={title}>
      <span className="text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
        {label}
      </span>
      <span className="tabular-nums text-[var(--fg)]">{value}</span>
    </div>
  );

  return (
    <div
      className="relative grid gap-x-5 gap-y-2 px-7 pb-3 pt-1 text-[length:var(--fs-micro)]"
      style={{ gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))" }}
    >
      {bid != null || ask != null
        ? cell(
            "quote @ print",
            `${bid != null ? bid.toFixed(2) : "—"} × ${ask != null ? ask.toFixed(2) : "—"}${hitSide ? ` · ${hitSide}` : ""}`,
          )
        : null}
      {spot != null || ev.otm_pct != null
        ? cell(
            "spot · otm",
            `${spot != null ? spot.toFixed(2) : "—"}${ev.otm_pct != null ? ` · ${Math.abs(ev.otm_pct).toFixed(1)}%` : ""}`,
          )
        : null}
      {/* long exchange lists (16+ on big sweeps) collapse to a count; full list on hover.
          premium + contracts are NOT repeated here — the row already shows them. */}
      {cell(
        "prints · exch",
        `${ev.n_prints ?? "—"}${
          ev.exchanges?.length
            ? ` · ${ev.exchanges.length > 4 ? `${ev.exchanges.length} exchanges` : ev.exchanges.join("·")}`
            : ""
        }`,
        ev.exchanges?.length ? ev.exchanges.join(" · ") : undefined,
      )}
      {ivPct != null ? cell("iv", `${ivPct.toFixed(0)}%`) : null}
      {delta != null || gamma != null
        ? cell("delta · gamma", `${delta != null ? delta.toFixed(2) : "—"} · ${gamma != null ? gamma.toFixed(3) : "—"}`)
        : null}
      {theta != null || vega != null
        ? cell("theta · vega", `${theta != null ? theta.toFixed(2) : "—"} · ${vega != null ? vega.toFixed(2) : "—"}`)
        : null}
      {oi != null
        ? cell(
            "open interest",
            `${Math.round(oi).toLocaleString()}${ev.vol_oi_ratio != null ? ` · vol/OI ${ev.vol_oi_ratio.toFixed(1)}×` : ""}`,
          )
        : null}
      {/* z-score exists on EOD unusual-volume events only — the intraday tape
          has no daily-volume baseline, shown as — rather than hidden */}
      {cell("z-score", ev.vol_zscore != null ? `${ev.vol_zscore.toFixed(1)}σ` : "—")}
      {classifiedFrac != null
        ? cell("classified", `${Math.round(classifiedFrac * 100)}% of premium`)
        : null}
      {/* cluster footprint (TWAP/iceberg instrumentation): high modal share +
          low cadence CV = uniform clips at a regular rhythm — one worked order? */}
      {modalShare != null
        ? cell(
            "footprint",
            `${Math.round(modalShare * 100)}% one clip size${cadenceCv != null ? ` · cv ${cadenceCv.toFixed(2)}` : ""}`,
          )
        : null}
      {cell("side basis", basis)}
      {cell(
        "score",
        `${score.toFixed(1)} = ${comps.map((c) => `+${c.pts.toFixed(1)}`).join(" ") || "—"}`,
      )}
      {why.length ? (
        <div className="col-span-full flex gap-2 border-t border-[var(--border)] pt-2 text-[var(--fg-dim)]">
          <span className="shrink-0 text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
            why
          </span>
          <span>{why.join(", ")}{!hasDetail ? " · (greeks pending)" : ""}</span>
        </div>
      ) : null}
    </div>
  );
}
