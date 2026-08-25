"use client";

import { useEffect, useState } from "react";

import {
  fetchCollectorStats,
  type CollectorLag,
  type CollectorSnapshot,
  type CollectorStats,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Live feed panel for the toolbar chips — the Rust collectors' 1 s snapshot
 * (`/api/collectors/stats`): rate now, latency now + today (p50/p90/p95/p99),
 * a per-minute sparkline, and the counters that matter when something is off.
 * Polls once a second while a chip's dropdown is open; every 30 s otherwise,
 * just enough for the chip's health dot to mean something (poll hygiene).
 */
export function useCollectorStats(open: boolean): {
  stats: CollectorStats | null;
  error: string | null;
} {
  const [stats, setStats] = useState<CollectorStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchCollectorStats()
        .then((s) => {
          if (cancelled) return;
          setStats(s);
          setError(null);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setError(e instanceof Error ? e.message : String(e));
        });
    };
    tick();
    const id = window.setInterval(tick, open ? 1000 : 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open]);
  return { stats, error };
}

function ms(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(1)}s`;
  if (v >= 100) return `${Math.round(v)}ms`;
  return `${v.toFixed(v < 10 ? 1 : 0)}ms`;
}

function num(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function rate(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}k/s`;
  return `${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}/s`;
}

function uptime(s: number | undefined): string {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** Latency thresholds: green ≤ 250 ms, amber ≤ 2 s, red beyond (queue lag). */
function lagTone(v: number | null | undefined): string {
  if (v == null) return "text-[var(--fg-faint)]";
  if (v <= 250) return "text-[var(--c-green)]";
  if (v <= 2000) return "text-[var(--c-amber)]";
  return "text-[var(--c-red)]";
}

function Sparkline({
  points,
  width = 232,
  height = 28,
}: {
  points: [number, number, number | null][];
  width?: number;
  height?: number;
}) {
  if (points.length < 2) {
    return (
      <div className="h-7 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
        sparkline after the first two minutes
      </div>
    );
  }
  const max = Math.max(1, ...points.map((p) => p[1]));
  const step = width / Math.max(1, points.length - 1);
  const d = points
    .map((p, i) => `${(i * step).toFixed(1)},${(height - (p[1] / max) * (height - 2) - 1).toFixed(1)}`)
    .join(" ");
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="block"
      aria-label={`messages per second by minute, peak ${rate(max)}`}
    >
      <polyline
        points={d}
        fill="none"
        stroke="var(--c-accent)"
        strokeWidth={1.25}
        strokeLinejoin="round"
      />
    </svg>
  );
}

function LagRow({ label, lag }: { label: string; lag: CollectorLag | undefined }) {
  return (
    <tr>
      <td className="pr-2 text-[var(--fg-faint)]">{label}</td>
      {(["p50", "p90", "p95", "p99"] as const).map((k) => (
        <td key={k} className={cn("px-1 text-right tabular-nums", lagTone(lag?.[k]))}>
          {ms(lag?.[k])}
        </td>
      ))}
      <td className="pl-1 text-right tabular-nums text-[var(--fg-dim)]">{ms(lag?.max)}</td>
    </tr>
  );
}

function Stat({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--fg-faint)]">{k}</span>
      <span className={cn("tabular-nums", tone ?? "text-foreground")}>{v}</span>
    </div>
  );
}

function Header({ snap, title }: { snap: CollectorSnapshot; title: string }) {
  const down = snap.stale || !snap.connected;
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 shrink-0 rounded-full",
            down ? "bg-[var(--c-red)]" : "bg-[var(--c-green-bg)] shadow-[0_0_8px_var(--c-green-bg)]",
          )}
        />
        <span className="font-bold tracking-[0.08em] uppercase">{title}</span>
        <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
          {snap.stale
            ? `stale ${snap.age_s != null ? `${Math.round(snap.age_s)}s` : ""}`
            : snap.connected
              ? `up ${uptime(snap.uptime_s)}`
              : (snap.feed_state ?? "disconnected")}
        </span>
      </div>
      <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
        {snap.rss_mb} MB
      </span>
    </div>
  );
}

function LagTable({ snap }: { snap: CollectorSnapshot }) {
  return (
    <table className="w-full text-[length:var(--fs-table)]">
      <thead>
        <tr className="text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
          <th className="pr-2 text-left font-normal">latency</th>
          <th className="px-1 text-right font-normal">p50</th>
          <th className="px-1 text-right font-normal">p90</th>
          <th className="px-1 text-right font-normal">p95</th>
          <th className="px-1 text-right font-normal">p99</th>
          <th className="pl-1 text-right font-normal">max</th>
        </tr>
      </thead>
      <tbody>
        <LagRow label="now" lag={snap.lag_ms?.now} />
        <LagRow label="today" lag={snap.lag_ms?.day} />
      </tbody>
    </table>
  );
}

export function EquityFeedPanel({ snap }: { snap: CollectorSnapshot | null }) {
  if (!snap) {
    return (
      <p className="text-[var(--fg-dim)]">
        No equity collector snapshot — is <span className="text-foreground">hmt-equity-collector</span> running?
      </p>
    );
  }
  const t = snap.by_type?.t ?? 0;
  const q = snap.by_type?.q ?? 0;
  return (
    <div className="space-y-2">
      <Header snap={snap} title="SIP tape · Rust" />
      <div className="flex items-baseline justify-between">
        <span className="text-[length:var(--fs-emph)] font-bold tabular-nums text-foreground">
          {rate(snap.rate_1s)}
        </span>
        <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
          {rate(snap.rate_60s)} avg 60s · {num(snap.msgs_total)} today
        </span>
      </div>
      <Sparkline points={snap.minutes ?? []} />
      <LagTable snap={snap} />
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[length:var(--fs-table)]">
        <Stat k="trades" v={num(t)} />
        <Stat k="quotes" v={num(q)} />
        <Stat k="events" v={num(snap.events?.persisted)} />
        <Stat k="sweeps" v={num(snap.events?.sweeps)} />
        <Stat k="velocity" v={num(snap.events?.velocity)} />
        <Stat k="5m bars" v={num(snap.events?.bars_rows)} />
        <Stat k="hub clients" v={num(snap.proxy?.clients)} tone={snap.proxy?.clients ? undefined : "text-[var(--c-amber)]"} />
        <Stat k="proxy drops" v={num(snap.proxy?.drops)} tone={snap.proxy?.drops ? "text-[var(--c-amber)]" : undefined} />
        <Stat k="reconnects" v={num(snap.reconnects)} tone={snap.reconnects ? "text-[var(--c-amber)]" : undefined} />
        <Stat k="vendor errors" v={num(snap.vendor_errors)} tone={snap.vendor_errors ? "text-[var(--c-red)]" : undefined} />
        {snap.recorder ? (
          <>
            <Stat k="tape frames" v={num(snap.recorder.frames)} />
            <Stat k="tape drops" v={num(snap.recorder.drops)} tone={snap.recorder.drops ? "text-[var(--c-red)]" : undefined} />
          </>
        ) : null}
      </div>
    </div>
  );
}

export function FlowFeedPanel({ snap }: { snap: CollectorSnapshot | null }) {
  if (!snap) {
    return (
      <p className="text-[var(--fg-dim)]">
        No flow collector snapshot — is <span className="text-foreground">hmt-flow-collector</span> running the Rust spine?
      </p>
    );
  }
  const p = snap.pairing;
  const codedPct =
    snap.prints_total && snap.coded != null ? (100 * snap.coded) / snap.prints_total : null;
  const enrich = p?.enrich_buckets;
  const fast = enrich && p?.matched ? (100 * (enrich.le10ms + enrich.le100ms)) / p.matched : null;
  return (
    <div className="space-y-2">
      <Header snap={snap} title="OPRA · Databento" />
      <div className="flex items-baseline justify-between">
        <span className="text-[length:var(--fs-emph)] font-bold tabular-nums text-foreground">
          {num(snap.prints_per_min)}
          <span className="ml-1 text-[length:var(--fs-micro)] font-normal text-[var(--fg-faint)]">prints/min</span>
        </span>
        <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
          {rate(snap.rate_1s)} now · {num(snap.prints_total)} today
        </span>
      </div>
      <Sparkline points={snap.minutes ?? []} />
      <LagTable snap={snap} />
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[length:var(--fs-table)]">
        <Stat k="mapped" v={num(snap.mapped)} />
        <Stat k="events" v={num(snap.flow_events)} />
        <Stat k="slow reader" v={num(snap.slow)} tone={snap.slow ? "text-[var(--c-red)]" : undefined} />
        <Stat k="gateway drops" v={num(snap.dropped)} tone={snap.dropped ? "text-[var(--c-red)]" : undefined} />
        <Stat k="reconnects" v={num(snap.reconnects)} tone={snap.reconnects ? "text-[var(--c-amber)]" : undefined} />
        <Stat k="gap" v={`${snap.gap_s ?? 0}s`} tone={snap.gap_s ? "text-[var(--c-amber)]" : undefined} />
        <Stat k="no meta" v={num(snap.no_meta)} />
        <Stat k="gauge rows" v={num(snap.minute_rows)} />
      </div>
      <div className="border-t border-[var(--border)] pt-2">
        <div className="text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
          condition codes · Alpaca pairing
        </div>
        {p ? (
          <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-[length:var(--fs-table)]">
            <Stat
              k="matched"
              v={`${p.matched_pct.toFixed(1)}%`}
              tone={p.matched_pct >= 85 ? "text-[var(--c-green)]" : p.matched_pct >= 70 ? "text-[var(--c-amber)]" : "text-[var(--c-red)]"}
            />
            <Stat k="coded pre-drain" v={codedPct == null ? "—" : `${codedPct.toFixed(1)}%`} />
            <Stat k="≤100ms" v={fast == null ? "—" : `${fast.toFixed(0)}%`} />
            <Stat k="repairs" v={num(p.repairs)} />
            <Stat k="pending d/a" v={`${num(p.pending_dbn)} / ${num(p.pending_alpaca)}`} />
            <Stat k="tolerance" v={num(p.tolerance)} />
            <Stat k="expired" v={num(p.expired)} tone={p.expired ? "text-[var(--c-amber)]" : undefined} />
            <Stat k="evicted" v={num(p.evicted)} tone={p.evicted ? "text-[var(--c-red)]" : undefined} />
            <Stat k="alpaca resets" v={num(p.resets)} tone={p.resets ? "text-[var(--c-amber)]" : undefined} />
            <Stat k="wide ack" v={p.acked_wide ? "yes" : "no"} tone={p.acked_wide ? undefined : "text-[var(--c-red)]"} />
            <Stat k="GTH written" v={num(p.gth_written)} />
            <Stat k="GTH dropped" v={num(p.gth_dropped)} />
          </div>
        ) : (
          <p className="mt-1 text-[var(--fg-dim)]">pairing off (no --pairing)</p>
        )}
      </div>
    </div>
  );
}
