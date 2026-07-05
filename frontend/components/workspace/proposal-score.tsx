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

function formatSignedPct(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function validatedBadgeProps(validated: string | undefined): {
  label: string;
  className: string;
} | null {
  switch (validated) {
    case "validated":
      return {
        label: "Validated",
        className:
          "border-[color-mix(in_srgb,var(--c-green)_35%,transparent)] text-[var(--c-green)]",
      };
    case "strong-unverified":
      return {
        label: "Strong · unverified",
        className:
          "border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] text-[var(--c-amber)]",
      };
    case "exploratory":
      return {
        label: "Exploratory",
        className:
          "border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] text-[var(--fg-dim)]",
      };
    default:
      return null;
  }
}

function StrategyBacktestScore({ score }: { score: ProposalScoreData }) {
  const badge = validatedBadgeProps(score.validated);
  const winRate = score.win_rate ?? score.win_prob;
  const hasWinRate = winRate != null && Number.isFinite(winRate);
  const evPct = score.ev_pct;
  const hasEv = evPct != null && Number.isFinite(evPct);
  const evCi = score.ev_ci_pct;
  const isExploratory = !hasWinRate;
  const planRules = score.plan?.rules ?? [];

  return (
    <div className="space-y-1.5 rounded-md border border-[var(--border)] bg-[var(--bg-inset)]/40 px-2.5 py-2 text-[length:var(--fs-body)]">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-foreground">{score.label ?? "Strategy"}</span>
        {badge ? (
          <Badge variant="outline" className={cn("h-4 px-1.5 text-[length:var(--fs-table)]", badge.className)}>
            {badge.label}
          </Badge>
        ) : null}
      </div>

      {isExploratory ? (
        <p className="text-[var(--fg-dim)]">No validated edge — exploratory</p>
      ) : (
        <>
          {hasWinRate ? (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[length:var(--fs-table)] uppercase tracking-[0.08em] text-[var(--fg-dim)]">
                  Win
                </span>
                <span className="font-semibold tabular-nums text-foreground">
                  {clampPercent(winRate).toFixed(0)}%
                </span>
              </div>
              <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-[var(--bg-inset)]">
                <div
                  className="absolute inset-y-0 left-0 rounded-full bg-[var(--c-accent)]"
                  style={{ width: `${clampPercent(winRate)}%` }}
                />
              </div>
            </>
          ) : null}

          {hasEv ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span
                className={cn(
                  "font-mono tabular-nums",
                  evPct >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
                )}
              >
                EV {formatSignedPct(evPct)}
              </span>
              {evCi != null && evCi.length === 2 ? (
                <span className="font-mono tabular-nums text-[var(--fg-dim)]">
                  · EV CI [{formatSignedPct(evCi[0])}, {formatSignedPct(evCi[1])}]
                </span>
              ) : null}
            </div>
          ) : null}
        </>
      )}

      {score.n_setups != null ? (
        <p className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          n={score.n_setups.toLocaleString()} setups
          {score.per_year_positive ? ` · ${score.per_year_positive} yrs+` : ""}
        </p>
      ) : null}

      {planRules.length > 0 ? (
        <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 border-t border-[var(--border)]/60 pt-1.5">
          <span className="text-[length:var(--fs-table)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
            Plan
          </span>
          {planRules.map((rule, i) => (
            <span key={rule} className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
              {rule}
              {i < planRules.length - 1 ? " ·" : ""}
            </span>
          ))}
        </div>
      ) : null}

      {score.note ? (
        <p className="truncate text-[length:var(--fs-table)] text-[var(--fg-faint)]" title={score.note}>
          {score.note}
        </p>
      ) : null}
    </div>
  );
}

function GenericScore({ score }: { score: ProposalScoreData }) {
  const winPct = clampPercent(score.win_prob);
  const baseRatePct = score.base_rate != null ? clampPercent(score.base_rate) : null;
  const evPct = score.ev_pct ?? null;
  const ciLow = score.ci_low != null ? (score.ci_low * 100).toFixed(0) : null;
  const ciHigh = score.ci_high != null ? (score.ci_high * 100).toFixed(0) : null;
  const factors = (score.top_factors ?? []).slice(0, 3);

  return (
    <div className="space-y-1.5 rounded-md border border-[var(--border)] bg-[var(--bg-inset)]/40 px-2.5 py-2 text-[length:var(--fs-body)]">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[length:var(--fs-table)] uppercase tracking-[0.08em] text-[var(--fg-dim)]">
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
        {baseRatePct != null ? (
          <div
            className="absolute top-0 bottom-0 w-px bg-[var(--fg-faint)] opacity-70"
            style={{ left: `${baseRatePct}%` }}
            title={`Base rate ${baseRatePct.toFixed(0)}%`}
          />
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {evPct != null ? (
          <span
            className={cn(
              "font-mono tabular-nums",
              evPct >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
            )}
          >
            EV {evPct >= 0 ? "+" : ""}
            {evPct.toFixed(2)}%
          </span>
        ) : null}
        {ciLow != null && ciHigh != null ? (
          <span className="font-mono tabular-nums text-[var(--fg-dim)]">
            CI {ciLow}–{ciHigh}%
          </span>
        ) : null}
        {score.low_confidence ? (
          <Badge
            variant="outline"
            className="h-4 px-1.5 text-[length:var(--fs-table)]"
            title={`Provisional — small sample (n=${score.n_samples ?? 0})`}
          >
            low confidence
          </Badge>
        ) : null}
        {!score.sufficient ? (
          <Badge variant="outline" className="h-4 px-1.5 text-[length:var(--fs-table)]">
            thin data
          </Badge>
        ) : null}
      </div>

      {factors.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">Why</span>
          {factors.map((factor) => {
            const bullish = factor.direction === "bullish";
            return (
              <span
                key={factor.name}
                className={cn(
                  "inline-flex items-center gap-0.5 rounded border px-1 py-px font-mono text-[length:var(--fs-table)]",
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

export function ProposalScore({ score }: Props) {
  if (score.basis === "strategy_backtest") {
    return <StrategyBacktestScore score={score} />;
  }
  return <GenericScore score={score} />;
}
