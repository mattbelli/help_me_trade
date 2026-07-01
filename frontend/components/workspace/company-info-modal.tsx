"use client";

import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  fetchTickerInfo,
  type TickerFacts,
  type TickerInfo,
  type TickerMetrics,
  type TickerProfile,
} from "@/lib/ticker-info-api";

const DASH = "—";

function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return DASH;
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return DASH;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

// Compact currency for an absolute dollar amount.
function fmtMoneyCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return DASH;
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
  return `$${value.toLocaleString()}`;
}

// Finnhub reports market cap / shares outstanding in MILLIONS.
function fmtMillions(value: number | null | undefined, currency = true): string {
  if (value == null || Number.isNaN(value)) return DASH;
  const abs = value * 1e6;
  if (!currency) {
    if (abs >= 1e9) return `${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${(abs / 1e6).toFixed(1)}M`;
    return abs.toLocaleString();
  }
  return fmtMoneyCompact(abs);
}

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  const mins = Math.round(diffSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function winProbLabel(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return DASH;
  // score_symbol emits win_prob as a 0..1 fraction.
  const pct = value <= 1 ? value * 100 : value;
  return `${pct.toFixed(0)}%`;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="mb-1.5 text-[9.5px] font-medium tracking-[0.06em] uppercase"
      style={{ color: "var(--fg-faint)" }}
    >
      {children}
    </div>
  );
}

function StatCell({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="flex flex-col gap-px">
      <span
        className="text-[9px] font-medium tracking-[0.04em] uppercase"
        style={{ color: "var(--fg-faint)" }}
      >
        {label}
      </span>
      <span
        className="tabular-nums text-[11px]"
        style={{ color: color ?? "var(--fg-dim)" }}
      >
        {value}
      </span>
    </div>
  );
}

function HeaderSection({
  symbol,
  profile,
  metrics,
  linked,
  onPublish,
}: {
  symbol: string;
  profile: TickerProfile;
  metrics: TickerMetrics;
  linked: boolean;
  onPublish: () => void;
}) {
  const marketCap = profile.marketCapitalization ?? metrics.market_cap;
  const meta = [profile.finnhubIndustry, profile.exchange].filter(Boolean);
  return (
    <div className="flex items-start gap-2.5">
      {profile.logo ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={profile.logo}
          alt=""
          className="mt-0.5 size-8 shrink-0 rounded bg-[var(--bg-inset)] object-contain"
        />
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <ClickableSymbol
            symbol={symbol}
            linked={linked}
            onActivate={onPublish}
            className="font-mono text-[13px] font-medium"
          />
          {profile.weburl ? (
            <a
              href={profile.weburl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 text-[10px] text-[var(--fg-faint)] transition-colors hover:text-[var(--c-accent)]"
              onClick={(e) => e.stopPropagation()}
            >
              site
              <ExternalLink className="size-2.5" />
            </a>
          ) : null}
        </div>
        {profile.name ? (
          <div className="truncate text-[12px] text-foreground">
            {profile.name}
          </div>
        ) : null}
        {meta.length > 0 || marketCap != null ? (
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-[var(--fg-dim)]">
            {meta.map((m) => (
              <span key={m}>{m}</span>
            ))}
            {marketCap != null ? (
              <span className="tabular-nums">{fmtMillions(marketCap)} cap</span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function FundamentalsSection({
  profile,
  metrics,
}: {
  profile: TickerProfile;
  metrics: TickerMetrics;
}) {
  const range =
    metrics.week_52_low != null && metrics.week_52_high != null
      ? `${fmtNum(metrics.week_52_low)} – ${fmtNum(metrics.week_52_high)}`
      : DASH;
  const items: { label: string; value: string }[] = [
    { label: "P/E", value: fmtNum(metrics.pe_ttm, 1) },
    { label: "52-wk range", value: range },
    { label: "Beta", value: fmtNum(metrics.beta, 2) },
    { label: "Div yield", value: fmtPct(metrics.div_yield) },
    { label: "Gross margin", value: fmtPct(metrics.gross_margin, 1) },
    { label: "Net margin", value: fmtPct(metrics.net_margin, 1) },
    { label: "EPS (TTM)", value: metrics.eps_ttm != null ? `$${fmtNum(metrics.eps_ttm)}` : DASH },
    { label: "Shares out", value: fmtMillions(profile.shareOutstanding, false) },
  ];
  const hasAny = items.some((i) => i.value !== DASH);
  if (!hasAny) return null;
  return (
    <div>
      <SectionTitle>Fundamentals</SectionTitle>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {items.map((i) => (
          <StatCell key={i.label} label={i.label} value={i.value} />
        ))}
      </div>
    </div>
  );
}

function FactsSection({ facts }: { facts: TickerFacts }) {
  const t = facts.technicals ?? {};
  const nodes = facts.session_nodes ?? {};
  const score = facts.decision_score ?? null;
  const trendColor =
    t.trend === "up"
      ? "var(--c-green)"
      : t.trend === "down"
        ? "var(--c-red)"
        : "var(--fg-dim)";

  const stats: { label: string; value: string; color?: string }[] = [
    {
      label: "RVOL",
      value: facts.rvol?.value != null ? `${facts.rvol.value.toFixed(1)}×` : DASH,
    },
    {
      label: "vs 200-DMA",
      value: fmtPct(t.vs_sma200_pct),
      color:
        t.vs_sma200_pct == null
          ? undefined
          : t.vs_sma200_pct >= 0
            ? "var(--c-green)"
            : "var(--c-red)",
    },
    { label: "RSI(14)", value: fmtNum(t.rsi14, 1) },
    { label: "ATR(14)", value: fmtNum(t.atr14, 2) },
    { label: "Trend", value: t.trend ?? DASH, color: trendColor },
    { label: "Win %", value: winProbLabel(score?.win_prob) },
    { label: "EV", value: fmtPct(score?.ev) },
    { label: "Next earns", value: facts.earnings?.next ?? DASH },
    { label: "Last earns", value: facts.earnings?.last ?? DASH },
  ];

  const levels: { label: string; value: number | null | undefined }[] = [
    { label: "POC", value: nodes.poc },
    { label: "VAH", value: nodes.vah },
    { label: "VAL", value: nodes.val },
    { label: "PDH", value: nodes.pdh },
    { label: "PDL", value: nodes.pdl },
  ];
  const hasLevels = levels.some((l) => l.value != null);

  return (
    <div>
      <SectionTitle>Our facts</SectionTitle>
      <div className="grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-4">
        {stats.map((s) => (
          <StatCell key={s.label} label={s.label} value={s.value} color={s.color} />
        ))}
      </div>
      {hasLevels ? (
        <div className="mt-2 grid grid-cols-5 gap-x-3 gap-y-1">
          {levels.map((l) => (
            <StatCell key={l.label} label={l.label} value={fmtNum(l.value)} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function NewsSection({ news }: { news: TickerInfo["news"] }) {
  return (
    <div>
      <SectionTitle>Recent news</SectionTitle>
      {news.length === 0 ? (
        <p className="text-[11px] text-[var(--fg-faint)]">No recent news</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {news.map((item) => (
            <li key={item.url} className="flex flex-col gap-0.5">
              <a
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-start gap-1 text-[11px] leading-snug text-foreground transition-colors hover:text-[var(--c-accent)]"
                onClick={(e) => e.stopPropagation()}
              >
                <span className="min-w-0">{item.headline}</span>
                <ExternalLink className="mt-0.5 size-2.5 shrink-0 text-[var(--fg-faint)]" />
              </a>
              <span className="text-[9.5px] text-[var(--fg-faint)]">
                {[item.source, relativeTime(item.datetime)]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {item.summary ? (
                <span className="line-clamp-2 text-[10px] text-[var(--fg-dim)]">
                  {item.summary}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function CompanyInfoModal({
  symbol,
  open,
  onClose,
  linked,
  onPublish,
}: {
  symbol: string | null;
  open: boolean;
  onClose: () => void;
  linked: boolean;
  onPublish: (symbol: string) => void;
}) {
  const [data, setData] = useState<TickerInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !symbol) return;
    let cancelled = false;
    setData(null);
    setError(null);
    setLoading(true);
    fetchTickerInfo(symbol)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load company info");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, symbol]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] w-full max-w-lg overflow-y-auto border border-[var(--border)] bg-[var(--panel)] font-mono text-foreground sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono text-[13px]">
            {symbol ?? "Company"}
          </DialogTitle>
        </DialogHeader>

        {loading ? (
          <p className="text-[11px] text-[var(--fg-dim)]">Loading…</p>
        ) : error ? (
          <p className="text-[11px] text-[var(--c-red)]">{error}</p>
        ) : data ? (
          <div className="flex flex-col gap-4">
            <HeaderSection
              symbol={data.symbol}
              profile={data.profile}
              metrics={data.metrics}
              linked={linked}
              onPublish={() => onPublish(data.symbol)}
            />
            <FundamentalsSection profile={data.profile} metrics={data.metrics} />
            <FactsSection facts={data.facts} />
            <NewsSection news={data.news} />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
