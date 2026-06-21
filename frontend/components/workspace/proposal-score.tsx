"use client";

import { Badge } from "@/components/ui/badge";
import type { ProposalScore as ProposalScoreData } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  score: ProposalScoreData;
};

function humanizeFactorName(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value * 100));
}

export function ProposalScore({ score }: Props) {
  const winPct = clampPercent(score.win_prob);
  const baseRatePct = clampPercent(score.base_rate ?? 0);
  const evPct = score.ev_pct ?? 0;
  const ciLow = score.ci_low != null ? (score.ci_low * 100).toFixed(0) : null;
  const ciHigh = score.ci_high != null ? (score.ci_high * 100).toFixed(0) : null;
  const factors = (score.top_factors ?? []).slice(0, 3);

  return (
    <div className="space-y-1.5 rounded-md border border-[var(--border)] bg-[var(--bg-inset)]/40 px-2.5 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wide text-[var(--fg-dim)]">
          Win
        </span>
        <span className="font-semibold tabular-nums text-foreground">
          {winPct.toFixed(0)}%
        </span>
      </div>

      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-[var(--bg-inset)]">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-[var(--c-accent)]"
          style={{ width: `${winPct}%` }}
        />
        <div
          className="absolute top-0 bottom-0 w-px bg-[var(--fg-faint)] opacity-70"
          style={{ left: `${baseRatePct}%` }}
          title={`Base rate ${baseRatePct.toFixed(0)}%`}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className={cn(
            "font-mono tabular-nums",
            evPct >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
          )}
        >
          EV {evPct >= 0 ? "+" : ""}
          {evPct.toFixed(2)}%
        </span>
        {ciLow != null && ciHigh != null ? (
          <span className="font-mono tabular-nums text-[var(--fg-dim)]">
            CI {ciLow}–{ciHigh}%
          </span>
        ) : null}
        {score.low_confidence ? (
          <Badge
            variant="outline"
            className="h-4 px-1.5 text-[10px]"
            title={`Provisional — small sample (n=${score.n_samples ?? 0})`}
          >
            low confidence
          </Badge>
        ) : null}
        {!score.sufficient ? (
          <Badge variant="outline" className="h-4 px-1.5 text-[10px]">
            thin data
          </Badge>
        ) : null}
      </div>

      {factors.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-[var(--fg-faint)]">Why</span>
          {factors.map((factor) => {
            const bullish = factor.direction === "bullish";
            return (
              <span
                key={factor.name}
                className={cn(
                  "inline-flex items-center gap-0.5 rounded border px-1 py-px font-mono text-[10px]",
                  bullish
                    ? "border-[color-mix(in_srgb,var(--c-green)_35%,transparent)] text-[var(--c-green)]"
                    : "border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] text-[var(--c-red)]",
                )}
              >
                {humanizeFactorName(factor.name)}
                <span aria-hidden>{bullish ? "↑" : "↓"}</span>
              </span>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
