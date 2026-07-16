"use client";

import { ExternalLink } from "lucide-react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { cn } from "@/lib/utils";
import type {
  TickerFacts,
  TickerInfo,
  TickerMetrics,
  TickerNewsItem,
  TickerProfile,
  TickerShortInterest,
} from "@/lib/ticker-info-api";

export const DASH = "—";

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return DASH;
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return DASH;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

// Compact currency for an absolute dollar amount.
export function fmtMoneyCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return DASH;
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
  return `$${value.toLocaleString()}`;
}

// Finnhub reports market cap / shares outstanding in MILLIONS.
export function fmtMillions(value: number | null | undefined, currency = true): string {
  if (value == null || Number.isNaN(value)) return DASH;
  const abs = value * 1e6;
  if (!currency) {
    if (abs >= 1e9) return `${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${(abs / 1e6).toFixed(1)}M`;
    return abs.toLocaleString();
  }
  return fmtMoneyCompact(abs);
}

export function relativeTime(iso: string | null): string {
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

export function winProbLabel(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return DASH;
  // score_symbol emits win_prob as a 0..1 fraction.
  const pct = value <= 1 ? value * 100 : value;
  return `${pct.toFixed(0)}%`;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="mb-1.5 text-[length:var(--fs-table)] font-medium tracking-[0.08em] uppercase"
      style={{ color: "var(--fg-faint)" }}
    >
      {children}
    </div>
  );
}

// Long venue strings ("NASDAQ NMS - GLOBAL MARKET") read as noise in a meta
// line — collapse to the household name.
function shortExchange(exchange: string | null | undefined): string | null {
  if (!exchange) return null;
  const upper = exchange.toUpperCase();
  if (upper.includes("NASDAQ")) return "NASDAQ";
  if (upper.includes("NEW YORK")) return "NYSE";
  return exchange.split(/[,-]/)[0].trim();
}

export type StatItem = {
  label: string;
  value: string;
  color?: string;
  title?: string;
};

/** Hairline-bordered cell grid — the md-viewer table treatment applied to
    stats: label over value in each cell, 1px rules from a gap-px trick. */
function StatGrid({
  items,
  cols = 4,
  className,
}: {
  items: StatItem[];
  cols?: 4 | 5;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-md border border-[var(--border)] bg-[var(--border)]",
        className,
      )}
    >
      <div
        className={cn(
          "grid gap-px",
          cols === 5 ? "grid-cols-5" : "grid-cols-2 sm:grid-cols-4",
        )}
      >
        {items.map((i) => (
          <div
            key={i.label}
            title={i.title}
            className="flex flex-col gap-0.5 bg-[var(--panel)] px-2.5 py-2"
          >
            <span
              className="truncate text-[length:var(--fs-micro)] font-medium tracking-[0.08em] uppercase"
              style={{ color: "var(--fg-faint)" }}
            >
              {i.label}
            </span>
            <span
              className="truncate text-[length:var(--fs-title)] font-semibold tabular-nums"
              style={{ color: i.color ?? "var(--fg)" }}
            >
              {i.value}
            </span>
          </div>
        ))}
        {/* Pad the last row so the border bg never shows through empty
            tracks. cols=4 also covers the 2-col narrow breakpoint (2 | 4). */}
        {Array.from(
          { length: (cols - (items.length % cols)) % cols },
          (_, i) => (
            <div key={`pad-${i}`} className="bg-[var(--panel)]" />
          ),
        )}
      </div>
    </div>
  );
}

export function HeaderSection({
  symbol,
  profile,
  metrics,
  facts,
  linked,
  onPublish,
}: {
  symbol: string;
  profile: TickerProfile;
  metrics: TickerMetrics;
  facts: TickerFacts;
  linked: boolean;
  onPublish: () => void;
}) {
  const meta = [profile.finnhubIndustry, shortExchange(profile.exchange)].filter(
    Boolean,
  );
  const quote = facts.quote;
  const changePct = quote?.change_pct;
  const changeColor =
    changePct == null
      ? "var(--fg-dim)"
      : changePct >= 0
        ? "var(--c-green)"
        : "var(--c-red)";
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
            className="font-mono text-[length:var(--fs-title)] font-medium"
          />
          {profile.weburl ? (
            <a
              href={profile.weburl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 text-[length:var(--fs-table)] text-[var(--fg-faint)] transition-colors hover:text-[var(--c-accent)]"
              onClick={(e) => e.stopPropagation()}
            >
              site
              <ExternalLink className="size-2.5" />
            </a>
          ) : null}
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className="font-mono text-[length:var(--fs-hero)] leading-none font-semibold tabular-nums text-foreground">
            {quote?.price != null ? `$${fmtNum(quote.price)}` : DASH}
          </span>
          <span
            className="text-[length:var(--fs-title)] font-semibold tabular-nums"
            style={{ color: changeColor }}
          >
            {fmtPct(changePct)}
          </span>
          {quote?.market_session ? (
            <span
              className="font-mono text-[length:var(--fs-micro)] lowercase tracking-[0.08em]"
              style={{ color: "var(--fg-faint)" }}
            >
              {quote.market_session}
            </span>
          ) : null}
        </div>
        {profile.name ? (
          <div className="mt-0.5 truncate text-[length:var(--fs-title)] text-foreground">
            {profile.name}
          </div>
        ) : null}
        {meta.length > 0 ? (
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[length:var(--fs-table)] text-[var(--fg-dim)]">
            {meta.map((m) => (
              <span key={m}>{m}</span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** 52-week range as a track with the current price marked on it. */
function RangeBar({
  low,
  high,
  price,
}: {
  low: number;
  high: number;
  price: number | null | undefined;
}) {
  const pct =
    price != null && high > low
      ? Math.min(100, Math.max(0, ((price - low) / (high - low)) * 100))
      : null;
  return (
    <div className="flex flex-col gap-0.5">
      <span
        className="text-[length:var(--fs-table)] font-medium tracking-[0.08em] uppercase"
        style={{ color: "var(--fg-faint)" }}
      >
        52-wk range
      </span>
      <div className="flex items-center gap-1.5">
        <span className="text-[length:var(--fs-table)] tabular-nums text-[var(--fg-dim)]">
          {fmtNum(low, high >= 100 ? 0 : 2)}
        </span>
        <div className="relative h-[3px] min-w-8 flex-1 rounded-full bg-[var(--bg-hover)]">
          {pct != null ? (
            <span
              className="absolute top-1/2 size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--c-accent)]"
              style={{ left: `${pct}%` }}
            />
          ) : null}
        </div>
        <span className="text-[length:var(--fs-table)] tabular-nums text-[var(--fg-dim)]">
          {fmtNum(high, high >= 100 ? 0 : 2)}
        </span>
      </div>
    </div>
  );
}

export function FundamentalsSection({
  profile,
  metrics,
  shortInterest,
  price,
}: {
  profile: TickerProfile;
  metrics: TickerMetrics;
  shortInterest?: TickerShortInterest;
  price?: number | null;
}) {
  const growthColor =
    metrics.revenue_growth_yoy == null
      ? undefined
      : metrics.revenue_growth_yoy >= 0
        ? "var(--c-green)"
        : "var(--c-red)";

  // Short interest as % of shares outstanding (float unavailable on free
  // sources — label stays honest about the denominator).
  const sharesOutAbs =
    profile.shareOutstanding != null ? profile.shareOutstanding * 1e6 : null;
  const shortShares = shortInterest?.short_shares ?? null;
  const shortPct =
    shortShares != null && sharesOutAbs != null && sharesOutAbs > 0
      ? (shortShares / sharesOutAbs) * 100
      : null;
  const shortValue =
    shortPct != null
      ? `${shortPct.toFixed(1)}% out${
          shortInterest?.days_to_cover != null
            ? ` · ${shortInterest.days_to_cover.toFixed(1)}d`
            : ""
        }`
      : DASH;
  const shortTitle = shortInterest?.settlement_date
    ? `FINRA consolidated short interest, settled ${shortInterest.settlement_date} (published ~2wk lag) · % of shares outstanding · d = days to cover`
    : undefined;

  const items: StatItem[] = [
    { label: "Mkt cap", value: fmtMillions(profile.marketCapitalization ?? metrics.market_cap) },
    { label: "P/E", value: fmtNum(metrics.pe_ttm, 1) },
    {
      label: "Rev growth",
      value: fmtPct(metrics.revenue_growth_yoy, 1),
      color: growthColor,
      title: "revenue growth, trailing twelve months YoY",
    },
    {
      label: "Short int",
      value: shortValue,
      color: shortPct != null && shortPct >= 10 ? "var(--c-amber)" : undefined,
      title: shortTitle,
    },
    { label: "Gross margin", value: fmtPct(metrics.gross_margin, 1) },
    { label: "Net margin", value: fmtPct(metrics.net_margin, 1) },
    { label: "EPS (TTM)", value: metrics.eps_ttm != null ? `$${fmtNum(metrics.eps_ttm)}` : DASH },
    { label: "Div yield", value: fmtPct(metrics.div_yield) },
    { label: "P/S", value: fmtNum(metrics.ps_ttm, 1) },
    { label: "ROE", value: fmtPct(metrics.roe_ttm, 1) },
    { label: "Beta", value: fmtNum(metrics.beta, 2) },
    { label: "Shares out", value: fmtMillions(profile.shareOutstanding, false) },
  ];
  const hasAny = items.some((i) => i.value !== DASH);
  if (!hasAny) return null;
  return (
    <div>
      <SectionTitle>Fundamentals</SectionTitle>
      {metrics.week_52_low != null && metrics.week_52_high != null ? (
        <div className="mb-3">
          <RangeBar
            low={metrics.week_52_low}
            high={metrics.week_52_high}
            price={price}
          />
        </div>
      ) : null}
      <StatGrid items={items} />
    </div>
  );
}

export function FactsSection({ facts }: { facts: TickerFacts }) {
  const t = facts.technicals ?? {};
  const nodes = facts.session_nodes ?? {};
  const score = facts.decision_score ?? null;
  const trendColor =
    t.trend === "up"
      ? "var(--c-green)"
      : t.trend === "down"
        ? "var(--c-red)"
        : "var(--fg-dim)";

  const stats: StatItem[] = [
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
      <StatGrid items={stats} />
      {hasLevels ? (
        <StatGrid
          className="mt-2"
          cols={5}
          items={levels.map((l) => ({ label: l.label, value: fmtNum(l.value) }))}
        />
      ) : null}
    </div>
  );
}

/** A news item plus an optional symbol tag, for cross-symbol (watchlist) feeds. */
export type NewsListItem = TickerNewsItem & { symbol?: string };

export function NewsSection({
  news,
  title = "Recent news",
  showSymbol = false,
}: {
  news: NewsListItem[] | TickerInfo["news"];
  title?: string;
  showSymbol?: boolean;
}) {
  return (
    <div>
      <SectionTitle>{title}</SectionTitle>
      {news.length === 0 ? (
        <p className="text-[length:var(--fs-body)] text-[var(--fg-faint)]">No recent news</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {news.map((item, idx) => {
            const symbol = showSymbol ? (item as NewsListItem).symbol : undefined;
            return (
              <li key={`${symbol ?? ""}-${item.url}-${idx}`} className="flex flex-col gap-0.5">
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-start gap-1 text-[length:var(--fs-body)] leading-snug text-foreground transition-colors hover:text-[var(--c-accent)]"
                  onClick={(e) => e.stopPropagation()}
                >
                  {symbol ? (
                    <span className="mt-0.5 shrink-0 font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-accent)] uppercase">
                      {symbol}
                    </span>
                  ) : null}
                  <span className="min-w-0">{item.headline}</span>
                  <ExternalLink className="mt-0.5 size-2.5 shrink-0 text-[var(--fg-faint)]" />
                </a>
                <span className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                  {[item.source, relativeTime(item.datetime)]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {item.summary ? (
                  <span className="line-clamp-2 text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                    {item.summary}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
