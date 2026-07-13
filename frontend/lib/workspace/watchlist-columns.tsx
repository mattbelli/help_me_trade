import type { ReactNode } from "react";

import { Sparkline } from "@/components/trading/sparkline";
import {
  formatMoney,
  type Position,
  type WatchlistQuote,
  type WatchlistSettings,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Row-density presets ─────────────────────────────────────────────────────
export type WatchlistDensity = "compact" | "comfortable" | "spacious";

export const DENSITY_OPTIONS: { key: WatchlistDensity; label: string }[] = [
  { key: "compact", label: "compact" },
  { key: "comfortable", label: "comfortable" },
  { key: "spacious", label: "spacious" },
];

export const DEFAULT_DENSITY: WatchlistDensity = "comfortable";

// Vertical padding applied to the header + every row for each density.
export const DENSITY_ROW_CLASS: Record<WatchlistDensity, string> = {
  compact: "py-0.5",
  comfortable: "py-1.5",
  spacious: "py-2.5",
};

// ── Column model ────────────────────────────────────────────────────────────
export type WatchlistCellContext = {
  quote: WatchlistQuote | undefined;
  position: Position | undefined;
  // ext-session-aware display values (match the pinned Last/Δ% behavior today)
  displayPrice: number;
  displayChangePct: number;
  positive: boolean;
  extBadge: { label: string; title: string } | null;
  priceFlash: "up" | "down" | null;
};

export type WatchlistColumnDef = {
  key: string;
  label: string;
  title: string;
  category: "price" | "volume" | "fundamentals" | "technicals" | "position" | "visual";
  defaultWidth: number;
  minWidth: number;
  align: "left" | "right";
  sortable: boolean;
  defaultVisible: boolean;
  sortValue?: (
    q: WatchlistQuote | undefined,
    pos: Position | undefined,
  ) => number | string | null;
  cell: (ctx: WatchlistCellContext) => ReactNode;
};

// ── Formatting helpers ──────────────────────────────────────────────────────
const DASH = <span className="text-[var(--fg-faint)]">—</span>;

function greenRed(positive: boolean): string {
  return positive ? "text-[var(--c-green)]" : "text-[var(--c-red)]";
}

function formatPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPrice(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function rvolColor(value: number): string {
  if (value >= 3) return "var(--c-red)";
  if (value >= 2) return "var(--c-amber)";
  if (value >= 1.5) return "var(--c-yellow)";
  return "var(--fg-dim)";
}

// Abbreviate a raw count: 1.3M / 12.4K / 2.1B / 980.
function abbreviateNumber(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return String(Math.round(value));
}

// Abbreviate a dollar amount: $1.2T / $45B / $980M.
function abbreviateMoney(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

// Whole days until an ISO date (YYYY-MM-DD); today = 0, null when unparseable.
// Exported so the widget's symbol earnings badge shares one implementation.
export function daysUntil(dateStr: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!match) return null;
  const [, year, month, day] = match;
  const target = new Date(Number(year), Number(month) - 1, Number(day));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round(
    (target.getTime() - today.getTime()) / (24 * 60 * 60 * 1000),
  );
}

function earningsColor(days: number): string {
  if (days <= 1) return "var(--c-red)";
  if (days <= 3) return "var(--c-amber)";
  if (days <= 14) return "var(--foreground)";
  return "var(--fg-dim)";
}

// Horizontal min–max track with a marker at `px`. Shared by the day-range and
// 52-week-range columns (identical rendering, different bounds). DASH when a
// bound or the price is missing or the range is degenerate.
function RangeBar({
  lo,
  hi,
  px,
  title,
}: {
  lo: number | null | undefined;
  hi: number | null | undefined;
  px: number | null | undefined;
  title: string;
}): ReactNode {
  if (lo == null || hi == null || px == null || hi <= lo) return DASH;
  const pos = Math.min(1, Math.max(0, (px - lo) / (hi - lo))) * 100;
  return (
    <div className="flex w-full items-center" title={title}>
      <div className="relative h-[2px] w-full bg-[var(--line-divider)]">
        <span
          className="absolute top-1/2 h-2 w-[2px] -translate-y-1/2 bg-[var(--text-accent)]"
          style={{ left: `${pos}%` }}
        />
      </div>
    </div>
  );
}

// Percent distance of `px` from a reference level: (px - ref) / ref * 100.
// Null when either side is missing or the reference is 0. Shared by the
// 52-week-high/low and SMA-distance columns (sortValue + cell).
function pctFrom(
  px: number | null | undefined,
  ref: number | null | undefined,
): number | null {
  if (px == null || ref == null || ref === 0) return null;
  return ((px - ref) / ref) * 100;
}

// Bid-ask spread as a percent of the mid. Null unless both sides are > 0.
function bidAskSpreadPct(q: WatchlistQuote | undefined): number | null {
  const bid = q?.bid;
  const ask = q?.ask;
  if (bid == null || ask == null || bid <= 0 || ask <= 0) return null;
  return ((ask - bid) / ((ask + bid) / 2)) * 100;
}

// Today's move in ATR units: (price - prev_close) / atr14. Null when a term is
// missing or ATR is 0. All derived calcs use quote.price (regular-session
// close), not the ext-session display price.
function atrMove(q: WatchlistQuote | undefined): number | null {
  const px = q?.price;
  const pc = q?.prev_close;
  const atr = q?.atr14;
  if (px == null || pc == null || atr == null || atr === 0) return null;
  return (px - pc) / atr;
}

// Position day P&L: (price - prev_close) * qty. Null unless both a prev close
// and a position exist.
function posDayPl(
  q: WatchlistQuote | undefined,
  pos: Position | undefined,
): number | null {
  if (!pos || q?.price == null || q?.prev_close == null) return null;
  return (q.price - q.prev_close) * pos.qty;
}

// ── The registry (canonical order after the pinned symbol column) ───────────
export const WATCHLIST_COLUMNS: WatchlistColumnDef[] = [
  {
    key: "change_pct",
    label: "Δ%",
    title: "Day change %",
    category: "price",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: true,
    sortValue: (q) => q?.change_pct ?? null,
    cell: ({ quote, displayChangePct, positive }) =>
      quote ? (
        <span className={cn("tabular-nums", greenRed(positive))}>
          {formatPct(displayChangePct)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "last",
    label: "Last",
    title: "Last price",
    category: "price",
    defaultWidth: 84,
    minWidth: 56,
    align: "right",
    sortable: true,
    defaultVisible: true,
    sortValue: (q) => q?.price ?? null,
    cell: ({ quote, displayPrice, positive, extBadge, priceFlash }) => (
      <div className="flex justify-end" title={extBadge?.title}>
        <span
          className={cn(
            "-mx-0.5 inline-flex w-fit items-center justify-end gap-0.5 rounded px-0.5 tabular-nums",
            quote ? greenRed(positive) : "text-foreground",
            priceFlash === "up" && "tick-flash-up",
            priceFlash === "down" && "tick-flash-down",
          )}
        >
          {quote ? formatPrice(displayPrice) : "—"}
          {extBadge ? (
            <span className="rounded px-0.5 font-mono text-[length:var(--fs-micro)] leading-none text-[var(--fg-dim)] ring-1 ring-[var(--border)]">
              {extBadge.label}
            </span>
          ) : null}
        </span>
      </div>
    ),
  },
  {
    key: "volume",
    label: "Vol",
    title: "Session volume",
    category: "volume",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: true,
    sortValue: (q) => q?.volume ?? null,
    cell: ({ quote }) =>
      quote?.volume != null ? (
        <span className="tabular-nums text-foreground">
          {abbreviateNumber(quote.volume)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "rvol",
    label: "RVOL",
    title: "Relative volume",
    category: "volume",
    defaultWidth: 52,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: true,
    sortValue: (q) => q?.rvol?.value ?? null,
    cell: ({ quote }) =>
      quote?.rvol?.value != null ? (
        <span
          className={cn(
            "tabular-nums",
            quote.rvol.state === "overnight" && "opacity-50",
          )}
          style={{ color: rvolColor(quote.rvol.value) }}
          title={
            quote.rvol.state === "overnight"
              ? "Yesterday's volume (pre-market)"
              : `${quote.rvol.value.toFixed(2)}× normal volume for this time of day`
          }
        >
          {quote.rvol.value.toFixed(1)}×
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "sparkline",
    label: "1d",
    title: "Intraday sparkline",
    category: "visual",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: false,
    defaultVisible: true,
    cell: ({ quote, positive }) => (
      <div className="flex justify-end">
        <Sparkline
          values={
            (quote?.intraday?.length ? quote.intraday : quote?.sparkline) ?? []
          }
          width={48}
          height={18}
          className={
            quote ? greenRed(positive) : "text-[var(--fg-faint)]"
          }
        />
      </div>
    ),
  },
  {
    key: "gap_pct",
    label: "Gap%",
    title: "Overnight gap %",
    category: "price",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.gap_pct ?? null,
    cell: ({ quote }) =>
      quote?.gap_pct != null ? (
        <span className={cn("tabular-nums", greenRed(quote.gap_pct >= 0))}>
          {formatPct(quote.gap_pct)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "prev_close",
    label: "PrevCl",
    title: "Previous close",
    category: "price",
    defaultWidth: 64,
    minWidth: 48,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.prev_close ?? null,
    cell: ({ quote }) =>
      quote?.prev_close != null ? (
        <span className="tabular-nums text-foreground">
          {formatPrice(quote.prev_close)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "day_range",
    label: "Range",
    title: "Day range",
    category: "price",
    defaultWidth: 76,
    minWidth: 48,
    align: "right",
    sortable: false,
    defaultVisible: false,
    cell: ({ quote }) => {
      const lo = quote?.day_low;
      const hi = quote?.day_high;
      const title =
        lo != null && hi != null ? `${formatPrice(lo)} – ${formatPrice(hi)}` : "";
      return <RangeBar lo={lo} hi={hi} px={quote?.price} title={title} />;
    },
  },
  {
    key: "day_high",
    label: "Hi",
    title: "Day high",
    category: "price",
    defaultWidth: 64,
    minWidth: 48,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.day_high ?? null,
    cell: ({ quote }) =>
      quote?.day_high != null ? (
        <span className="tabular-nums text-foreground">
          {formatPrice(quote.day_high)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "day_low",
    label: "Lo",
    title: "Day low",
    category: "price",
    defaultWidth: 64,
    minWidth: 48,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.day_low ?? null,
    cell: ({ quote }) =>
      quote?.day_low != null ? (
        <span className="tabular-nums text-foreground">
          {formatPrice(quote.day_low)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "market_cap",
    label: "MktCap",
    title: "Market cap",
    category: "fundamentals",
    defaultWidth: 64,
    minWidth: 48,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.market_cap ?? null,
    cell: ({ quote }) =>
      quote?.market_cap != null ? (
        <span className="tabular-nums text-foreground">
          {abbreviateMoney(quote.market_cap)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "pe",
    label: "P/E",
    title: "Price / earnings",
    category: "fundamentals",
    defaultWidth: 52,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.pe ?? null,
    cell: ({ quote }) =>
      quote?.pe != null ? (
        <span className="tabular-nums text-foreground">
          {quote.pe.toFixed(1)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "eps",
    label: "EPS",
    title: "Earnings per share",
    category: "fundamentals",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.eps ?? null,
    cell: ({ quote }) =>
      quote?.eps != null ? (
        <span className="tabular-nums text-foreground">
          {quote.eps.toFixed(2)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "div_yield",
    label: "Yield",
    title: "Dividend yield",
    category: "fundamentals",
    defaultWidth: 52,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.div_yield ?? null,
    cell: ({ quote }) =>
      quote?.div_yield != null ? (
        <span className="tabular-nums text-foreground">
          {`${quote.div_yield.toFixed(2)}%`}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "earnings_in",
    label: "Earn",
    title: "Days to earnings",
    category: "fundamentals",
    defaultWidth: 52,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) =>
      q?.next_earnings != null ? daysUntil(q.next_earnings) : null,
    cell: ({ quote }) => {
      const days =
        quote?.next_earnings != null ? daysUntil(quote.next_earnings) : null;
      if (days == null) return DASH;
      return (
        <span
          className="tabular-nums"
          style={{ color: earningsColor(days) }}
          title={`Earnings ${quote?.next_earnings}`}
        >
          {`${days}d`}
        </span>
      );
    },
  },
  {
    key: "pos_qty",
    label: "Qty",
    title: "Position quantity",
    category: "position",
    defaultWidth: 52,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (_q, pos) => pos?.qty ?? null,
    cell: ({ position }) =>
      position ? (
        <span className="tabular-nums text-foreground">
          {position.qty.toLocaleString(undefined, {
            maximumFractionDigits: 3,
          })}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "pos_avg",
    label: "Avg",
    title: "Average entry price",
    category: "position",
    defaultWidth: 64,
    minWidth: 48,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (_q, pos) => pos?.avg_entry_price ?? null,
    cell: ({ position }) =>
      position ? (
        <span className="tabular-nums text-foreground">
          {formatPrice(position.avg_entry_price)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "pos_pl",
    label: "P&L",
    title: "Unrealized P&L ($)",
    category: "position",
    defaultWidth: 68,
    minWidth: 52,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (_q, pos) => pos?.unrealized_pl ?? null,
    cell: ({ position }) =>
      position ? (
        <span
          className={cn("tabular-nums", greenRed(position.unrealized_pl >= 0))}
        >
          {formatMoney(position.unrealized_pl)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "pos_pl_pct",
    label: "P&L%",
    title: "Unrealized P&L (%)",
    category: "position",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (_q, pos) => {
      if (!pos) return null;
      const cost = pos.avg_entry_price * pos.qty;
      return cost ? (pos.unrealized_pl / cost) * 100 : null;
    },
    cell: ({ position }) => {
      if (!position) return DASH;
      const cost = position.avg_entry_price * position.qty;
      if (!cost) return DASH;
      const pct = (position.unrealized_pl / cost) * 100;
      return (
        <span className={cn("tabular-nums", greenRed(pct >= 0))}>
          {formatPct(pct)}
        </span>
      );
    },
  },
  {
    key: "bid",
    label: "Bid",
    title: "Bid price",
    category: "price",
    defaultWidth: 64,
    minWidth: 48,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.bid ?? null,
    cell: ({ quote }) =>
      quote?.bid != null ? (
        <span className="tabular-nums text-foreground">
          {formatPrice(quote.bid)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "ask",
    label: "Ask",
    title: "Ask price",
    category: "price",
    defaultWidth: 64,
    minWidth: 48,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.ask ?? null,
    cell: ({ quote }) =>
      quote?.ask != null ? (
        <span className="tabular-nums text-foreground">
          {formatPrice(quote.ask)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "spread",
    label: "Sprd",
    title: "Bid-ask spread %",
    category: "price",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => bidAskSpreadPct(q),
    cell: ({ quote }) => {
      const v = bidAskSpreadPct(quote);
      if (v == null) return DASH;
      return (
        <span className="tabular-nums text-foreground">
          {`${v.toFixed(2)}%`}
        </span>
      );
    },
  },
  {
    key: "vs_spy",
    label: "vsSPY",
    title: "Day change vs SPY",
    category: "technicals",
    defaultWidth: 60,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => q?.vs_spy ?? null,
    cell: ({ quote }) =>
      quote?.vs_spy != null ? (
        <span className={cn("tabular-nums", greenRed(quote.vs_spy >= 0))}>
          {formatPct(quote.vs_spy)}
        </span>
      ) : (
        DASH
      ),
  },
  {
    key: "atr_move",
    label: "×ATR",
    title: "Move vs typical (ATR)",
    category: "technicals",
    defaultWidth: 56,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => atrMove(q),
    cell: ({ quote }) => {
      const v = atrMove(quote);
      if (v == null) return DASH;
      const sign = v > 0 ? "+" : "";
      return (
        <span
          className={cn("tabular-nums", greenRed(v >= 0))}
          title="today's move ÷ 14-day ATR"
        >
          {`${sign}${v.toFixed(1)}×`}
        </span>
      );
    },
  },
  {
    key: "range_52w",
    label: "52w",
    title: "52-week range",
    category: "technicals",
    defaultWidth: 76,
    minWidth: 48,
    align: "right",
    sortable: false,
    defaultVisible: false,
    cell: ({ quote }) => {
      const lo = quote?.w52_low;
      const hi = quote?.w52_high;
      const title =
        lo != null && hi != null ? `${formatPrice(lo)} – ${formatPrice(hi)}` : "";
      return <RangeBar lo={lo} hi={hi} px={quote?.price} title={title} />;
    },
  },
  {
    key: "dist_52w_high",
    label: "52wH",
    title: "From 52-week high",
    category: "technicals",
    defaultWidth: 60,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => pctFrom(q?.price, q?.w52_high),
    cell: ({ quote }) => {
      const v = pctFrom(quote?.price, quote?.w52_high);
      if (v == null) return DASH;
      return (
        <span
          className={cn(
            "tabular-nums",
            v >= 0 ? "text-[var(--c-green)]" : "text-foreground",
          )}
        >
          {formatPct(v)}
        </span>
      );
    },
  },
  {
    key: "dist_52w_low",
    label: "52wL",
    title: "From 52-week low",
    category: "technicals",
    defaultWidth: 60,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => pctFrom(q?.price, q?.w52_low),
    cell: ({ quote }) => {
      const v = pctFrom(quote?.price, quote?.w52_low);
      if (v == null) return DASH;
      return (
        <span
          className={cn(
            "tabular-nums",
            v <= 0 ? "text-[var(--c-red)]" : "text-foreground",
          )}
        >
          {formatPct(v)}
        </span>
      );
    },
  },
  {
    key: "sma50_dist",
    label: "SMA50",
    title: "From 50-day average",
    category: "technicals",
    defaultWidth: 60,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => pctFrom(q?.price, q?.sma50),
    cell: ({ quote }) => {
      const v = pctFrom(quote?.price, quote?.sma50);
      if (v == null) return DASH;
      return (
        <span className={cn("tabular-nums", greenRed(v >= 0))}>
          {formatPct(v)}
        </span>
      );
    },
  },
  {
    key: "sma200_dist",
    label: "SMA200",
    title: "From 200-day average",
    category: "technicals",
    defaultWidth: 64,
    minWidth: 44,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q) => pctFrom(q?.price, q?.sma200),
    cell: ({ quote }) => {
      const v = pctFrom(quote?.price, quote?.sma200);
      if (v == null) return DASH;
      return (
        <span className={cn("tabular-nums", greenRed(v >= 0))}>
          {formatPct(v)}
        </span>
      );
    },
  },
  {
    key: "pos_day_pl",
    label: "DayP&L",
    title: "Position day P&L",
    category: "position",
    defaultWidth: 68,
    minWidth: 52,
    align: "right",
    sortable: true,
    defaultVisible: false,
    sortValue: (q, pos) => posDayPl(q, pos),
    cell: ({ quote, position }) => {
      const v = posDayPl(quote, position);
      if (v == null) return DASH;
      return (
        <span className={cn("tabular-nums", greenRed(v >= 0))}>
          {formatMoney(v)}
        </span>
      );
    },
  },
];

// ── Resolved (merged) columns ───────────────────────────────────────────────
export type ResolvedWatchlistColumn = {
  def: WatchlistColumnDef;
  visible: boolean;
  width: number;
};

const MAX_COL_WIDTH = 600;

function clampWidth(width: number, min: number): number {
  return Math.min(MAX_COL_WIDTH, Math.max(min, width));
}

/**
 * Merge a persisted settings blob with the registry: saved entries first (in
 * saved order, unknown keys dropped), then any registry columns absent from the
 * saved list appended in registry order with their defaults — so newly-shipped
 * columns surface in the settings panel for old saved configs. No settings →
 * registry defaults.
 */
export function resolveWatchlistColumns(
  settings?: WatchlistSettings,
): ResolvedWatchlistColumn[] {
  const byKey = new Map(WATCHLIST_COLUMNS.map((def) => [def.key, def]));
  const result: ResolvedWatchlistColumn[] = [];
  const seen = new Set<string>();

  for (const saved of settings?.columns ?? []) {
    const def = byKey.get(saved.key);
    if (!def || seen.has(saved.key)) continue;
    seen.add(saved.key);
    result.push({
      def,
      visible: saved.visible,
      width: clampWidth(saved.width ?? def.defaultWidth, def.minWidth),
    });
  }

  for (const def of WATCHLIST_COLUMNS) {
    if (seen.has(def.key)) continue;
    result.push({ def, visible: def.defaultVisible, width: def.defaultWidth });
  }

  return result;
}
