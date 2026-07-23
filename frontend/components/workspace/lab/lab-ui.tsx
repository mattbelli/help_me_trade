"use client";

// Strategy Lab — shared visual primitives, ported from the approved A2 mockup
// grammar onto the app's theme tokens (so the page tracks any active theme).
// Type-scale map (A2 → app): title=--fs-hero, metric=--fs-emph, body=--fs-body,
// sub=--fs-table, label/honesty=--fs-micro. Categorical state is a plain
// lowercase colored word (never a pill). Saturated hue is rationed to
// direction / P&L; structure stays greyscale.

import type { ReactNode } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { cn } from "@/lib/utils";

// ── formatters (tabular-nums everywhere; ASCII sign keeps columns aligned) ──
export function money(
  v: number,
  opts?: { sign?: boolean; cents?: boolean },
): string {
  const digits = opts?.cents ? 2 : 0;
  const body = Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const sign = v < 0 ? "-" : opts?.sign ? "+" : "";
  return `${sign}$${body}`;
}

export function pct(
  v: number | null | undefined,
  opts?: { sign?: boolean; digits?: number },
): string {
  if (v == null || Number.isNaN(v)) return "—";
  const digits = opts?.digits ?? 2;
  const sign = v < 0 ? "-" : opts?.sign ? "+" : "";
  return `${sign}${Math.abs(v).toFixed(digits)}%`;
}

/** CSS color var for a P&L-ish value (null → faint, 0 → dim, ± → pos/neg). */
export function tone(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "var(--fg-faint)";
  if (v > 0) return "var(--pos)";
  if (v < 0) return "var(--neg)";
  return "var(--fg-dim)";
}

// ── section scaffolding ─────────────────────────────────────────────────────
export function SecHead({
  label,
  note,
  meta,
  className,
}: {
  label: string;
  note?: ReactNode;
  meta?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mb-2 flex items-baseline gap-3 border-b border-[var(--line-divider)] pb-1.5",
        className,
      )}
    >
      <span className="text-[length:var(--fs-micro)] font-semibold uppercase tracking-[0.11em] text-[var(--fg-faint)]">
        {label}
      </span>
      {note ? (
        <span className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          {note}
        </span>
      ) : null}
      {meta ? (
        <span className="ml-auto text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          {meta}
        </span>
      ) : null}
    </div>
  );
}

// ── KPI / stat strip (one hairline box, internal dividers, flat) ────────────
export function StatStrip({ children }: { children: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap border border-[var(--line-divider)]">
      {children}
    </div>
  );
}

export function StatBox({
  k,
  v,
  s,
  color,
}: {
  k: ReactNode;
  v: ReactNode;
  s?: ReactNode;
  color?: string;
}) {
  return (
    <div className="min-w-[104px] flex-1 border-r border-[var(--line-divider)] px-3 py-2 last:border-r-0">
      <div className="mb-0.5 text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
        {k}
      </div>
      <div
        className="text-[length:var(--fs-emph)] tabular-nums leading-tight"
        style={color ? { color } : undefined}
      >
        {v}
      </div>
      {s ? (
        <div className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          {s}
        </div>
      ) : null}
    </div>
  );
}

// ── honesty motif ───────────────────────────────────────────────────────────
/** Amber dotted hedge — the load-bearing "this is not calibrated" mark. */
export function Hedge({
  children,
  title,
}: {
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className="cursor-help border-b border-dotted border-[color-mix(in_srgb,var(--warn)_55%,transparent)] text-[length:var(--fs-micro)] tracking-[0.02em] text-[var(--warn)]"
    >
      {children}
    </span>
  );
}

export function Calib({ children }: { children: ReactNode }) {
  return (
    <span className="text-[length:var(--fs-micro)] tracking-[0.02em] text-[color-mix(in_srgb,var(--pos)_78%,var(--surface-canvas))]">
      {children}
    </span>
  );
}

/** The amber left-edge honesty bar (used atop the backtesting tab). */
export function HonestyBar({ children }: { children: ReactNode }) {
  return (
    <div className="mb-4 flex gap-2.5 border-l-2 border-[var(--warn)] bg-[var(--surface-widget)] px-3.5 py-2.5 text-[length:var(--fs-body)] text-[var(--fg-dim)]">
      <span className="leading-none text-[var(--warn)]">⚠</span>
      <span>{children}</span>
    </div>
  );
}

// ── plain-word categorical tag (never a pill) ───────────────────────────────
const TAG_COLOR: Record<string, string> = {
  armed: "var(--pos)",
  disarmed: "var(--fg-faint)",
  propose: "var(--c-accent)",
  auto: "var(--warn)",
  live: "color-mix(in srgb, var(--pos) 72%, var(--surface-canvas))",
  parked: "var(--fg-faint)",
  probation: "var(--warn)",
  mech: "var(--fg-dim)",
  llm: "var(--c-violet)",
  human: "var(--c-violet)",
  clear: "color-mix(in srgb, var(--pos) 72%, var(--surface-canvas))",
  neutral: "var(--fg-dim)",
};

export function Tag({
  children,
  kind = "neutral",
  title,
}: {
  children: ReactNode;
  kind?: keyof typeof TAG_COLOR | string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className="text-[length:var(--fs-table)] tracking-[0.01em]"
      style={{ color: TAG_COLOR[kind] ?? "var(--fg-dim)" }}
    >
      {children}
    </span>
  );
}

// ── bordered module (the cockpit "widgeted or bordered" discipline) ─────────
export function Frame({
  children,
  className,
  attn,
}: {
  children: ReactNode;
  className?: string;
  attn?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col border border-[var(--line-divider)]",
        attn && "border-l-2 border-l-[var(--c-accent)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function ModuleHead({
  label,
  count,
  countColor,
  meta,
}: {
  label: string;
  count?: ReactNode;
  countColor?: string;
  meta?: ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-2 border-b border-[var(--border-strong)] px-3 py-2">
      <span className="text-[length:var(--fs-micro)] font-semibold uppercase tracking-[0.11em] text-[var(--fg-dim)]">
        {label}
      </span>
      {count != null ? (
        <span
          className="text-[length:var(--fs-body)] font-semibold tabular-nums"
          style={countColor ? { color: countColor } : undefined}
        >
          {count}
        </span>
      ) : null}
      {meta ? (
        <span className="ml-auto whitespace-nowrap text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          {meta}
        </span>
      ) : null}
    </div>
  );
}

export function ModuleFoot({ children }: { children: ReactNode }) {
  return (
    <div className="mt-auto border-t border-[var(--line-divider)] px-3 py-1.5 text-[length:var(--fs-table)] text-[var(--fg-faint)]">
      {children}
    </div>
  );
}

// ── clickable ticker (house rule: every rendered symbol focuses the chart) ──
export function Ticker({
  symbol,
  className,
}: {
  symbol: string;
  className?: string;
}) {
  return (
    <ClickableSymbol
      symbol={symbol}
      linked
      onActivate={() => useWorkspaceStore.getState().focusSymbol(symbol)}
      className={cn("font-mono", className)}
    />
  );
}

// ── small states ────────────────────────────────────────────────────────────
export function EmptyNote({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 py-3 text-[length:var(--fs-table)] text-[var(--fg-faint)]">
      {children}
    </div>
  );
}

export function LoadingNote({ label = "loading…" }: { label?: string }) {
  return (
    <div className="px-3 py-3 text-[length:var(--fs-table)] text-[var(--fg-faint)]">
      {label}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 py-3 text-[length:var(--fs-table)] text-[var(--neg)]">
      {children}
    </div>
  );
}

// shared table class strings (A2 workhorse table on app tokens)
export const TH =
  "text-left text-[length:var(--fs-micro)] font-medium uppercase tracking-[0.07em] text-[var(--fg-faint)] whitespace-nowrap px-2.5 pb-1.5 pt-1 border-b border-[var(--border-strong)]";
export const TH_NUM = `${TH} text-right`;
export const TD =
  "px-2.5 py-1.5 border-b border-[var(--line-divider)] align-middle text-[length:var(--fs-body)]";
export const TD_NUM = `${TD} text-right tabular-nums whitespace-nowrap`;
