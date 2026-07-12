"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Bell, BellOff } from "lucide-react";

import type { NotificationSeverity } from "@/lib/api";
import { relativeTime } from "@/lib/relative-time";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  unreadCount,
  type WorkspaceNotification,
  useNotificationStore,
} from "@/lib/workspace/use-notification-store";
import { useNotificationActivate } from "@/lib/workspace/use-notification-activate";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Routing categories — stable filter buckets instead of one button per raw
// kind. Unknown kinds land in "system" so nothing ever disappears.
// ---------------------------------------------------------------------------
const CATEGORIES = ["trading", "flow", "alerts", "research", "system"] as const;
type Category = (typeof CATEGORIES)[number];
type FilterMode = "all" | "unread" | Category;

const KIND_CATEGORY: Record<string, Category> = {
  proposal: "trading",
  proposal_missed: "trading",
  proposal_reconciled: "trading",
  execution: "trading",
  fill: "trading",
  fill_report: "trading",
  exit: "trading",
  risk_block: "trading",
  risk_breaker: "trading",
  kill_switch: "trading",
  entries_halt: "trading",
  rebalance: "trading",
  strategy: "trading",
  auto: "trading",
  invalidation: "trading",
  runner_extend: "trading",
  convergence: "flow",
  options_flow_solo: "flow",
  price_alert: "alerts",
  rvol: "alerts",
  rule_triggered: "alerts",
  earnings: "alerts",
  report_ready: "research",
  research_ready: "research",
  scan_ready: "research",
  idea_triggered: "research",
  idea_resolved: "research",
  morning_brief: "research",
  evening_brief: "research",
  on_request: "research",
};

function categoryOf(kind: string): Category {
  return KIND_CATEGORY[kind] ?? "system";
}

function formatKind(kind: string): string {
  return kind.replace(/_/g, " ");
}

// Kind renders as a plain lowercase colored word — never a bordered chip.
function kindWordClass(kind: string): string {
  if (
    kind === "kill_switch" ||
    kind === "entries_halt" ||
    kind.startsWith("risk")
  ) {
    return "text-[var(--c-red)]";
  }
  switch (categoryOf(kind)) {
    case "trading":
      return "text-[var(--c-accent)]";
    case "flow":
      return "text-[var(--c-violet)]";
    case "alerts":
      return "text-[var(--c-amber)]";
    case "research":
      return "text-[var(--c-blue)]";
    default:
      return "text-[var(--fg-faint)]";
  }
}

// Only non-info severities earn a stripe — the mockup's cards are calm by
// default and let magnitude/side carry the color.
function severityStripeClass(severity: NotificationSeverity): string | null {
  switch (severity) {
    case "success":
      return "border-l-2 border-l-[var(--c-green)]";
    case "warning":
      return "border-l-2 border-l-[var(--c-amber)]";
    case "critical":
      return "border-l-2 border-l-[var(--c-red)]";
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Headline parsing — the card leads with a bold mono ticker and highlights
// magnitudes ($4.1M, 6.3×, 5.9σ) in accent, per the mockup's headline tier.
// ---------------------------------------------------------------------------
const NOT_TICKERS = new Set(["RVOL", "EOD", "AI", "EPS", "ETF", "OI", "DTE"]);

function symbolOf(n: WorkspaceNotification): string | null {
  const d = (n.data ?? {}) as Record<string, unknown>;
  const fromData = d.underlying ?? d.symbol;
  if (typeof fromData === "string" && fromData) return fromData.toUpperCase();
  const m = /^([A-Z]{1,5})(?=[:\s])/.exec(n.title);
  if (m && !NOT_TICKERS.has(m[1])) return m[1];
  return null;
}

function headlineText(n: WorkspaceNotification, symbol: string | null): string {
  let t = n.title;
  if (symbol) {
    const stripped = t.replace(new RegExp(`^${symbol}\\s*[:—-]?\\s*`), "");
    if (stripped) t = stripped;
  }
  return t;
}

const MAG_RE = /(\$[\d,.]+[KMB]?|\d+(?:\.\d+)?\s?[x×]\b|\d+(?:\.\d+)?σ)/g;

function highlightMagnitudes(text: string): ReactNode[] {
  const parts = text.split(MAG_RE);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <span key={i} className="font-medium text-[var(--c-accent)]">
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

function formatClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d
    .toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    })
    .replace(" AM", "a")
    .replace(" PM", "p");
}

// ---------------------------------------------------------------------------
// Detail facts — expansion renders labeled one-fact lines (mockup detail
// card), not a longer version of the same ·-joined string. Known payloads get
// real labels; everything else falls back to splitting the body per fact.
// ---------------------------------------------------------------------------
type FactLine = { label: string | null; value: string };

// Bodies range from clean ·-joined facts to raw markdown report excerpts —
// strip the markdown noise so a fact line reads as a fact.
function cleanFact(text: string): string {
  return text
    .replace(/^[#>\-*•\s]+/, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*/g, "")
    .trim();
}

const MAX_FACT_LINES = 8;

function factLines(n: WorkspaceNotification): {
  facts: FactLine[];
  caveat: string | null;
} {
  const d = (n.data ?? {}) as Record<string, unknown>;
  const facts: FactLine[] = [];
  let caveat: string | null = null;

  if (Array.isArray(d.components) && d.components.length > 0) {
    facts.push({ label: "signals", value: d.components.join(" + ") });
  }
  if (typeof d.score === "number") {
    facts.push({ label: "score", value: d.score.toFixed(1) });
  }
  if (Array.isArray(d.drivers) && d.drivers.length > 0) {
    facts.push({ label: "drivers", value: d.drivers.join(" · ") });
  }
  if (typeof d.reason === "string" && d.reason) {
    facts.push({ label: "why", value: d.reason });
  }
  if (typeof d.caveat === "string" && d.caveat) {
    caveat = d.caveat;
  }

  if (facts.length === 0 && n.body) {
    for (const piece of n.body.split(/\s*·\s*|\n+/)) {
      const value = cleanFact(piece);
      if (value) facts.push({ label: null, value });
      if (facts.length >= MAX_FACT_LINES) break;
    }
  }
  return { facts, caveat };
}

// Collapsed cards keep one compact fact line under the headline — titles
// alone ("signal confluence") say nothing without the $/× magnitudes that
// live in the body.
function previewText(n: WorkspaceNotification): string | null {
  if (!n.body) return null;
  const pieces = n.body
    .split(/\s*·\s*|\n+/)
    .map(cleanFact)
    .filter(Boolean);
  if (pieces.length === 0) return null;
  const joined = pieces.slice(0, 3).join(" · ");
  return joined === n.title ? null : joined;
}

// ---------------------------------------------------------------------------
// Card grouping — correlated fires (same group_key) collapse into one card
// with a count. An item joins while it is within the window of the group's
// oldest member, so a steady drip chains into a single card.
// ---------------------------------------------------------------------------
const GROUP_WINDOW_MS = 45 * 60_000;

type Card = { key: string; items: WorkspaceNotification[] };

function groupCards(items: WorkspaceNotification[]): Card[] {
  const cards: Card[] = [];
  const open = new Map<string, Card>();
  for (const n of items) {
    const gk = n.group_key;
    if (gk) {
      const g = open.get(gk);
      if (g) {
        const oldest = g.items[g.items.length - 1];
        if (
          Date.parse(oldest.created_at) - Date.parse(n.created_at) <=
          GROUP_WINDOW_MS
        ) {
          g.items.push(n);
          continue;
        }
      }
      const card: Card = { key: `${gk}:${n.id}`, items: [n] };
      open.set(gk, card);
      cards.push(card);
    } else {
      cards.push({ key: n.id, items: [n] });
    }
  }
  return cards;
}

// ---------------------------------------------------------------------------
// Ding — a short two-note blip when a new card lands. Browsers gate audio on
// a prior user gesture; until then this silently no-ops.
// ---------------------------------------------------------------------------
let dingCtx: AudioContext | null = null;

function playDing(): void {
  try {
    dingCtx ??= new AudioContext();
    if (dingCtx.state === "suspended") void dingCtx.resume();
    if (dingCtx.state !== "running") return;
    const t0 = dingCtx.currentTime;
    for (const [freq, at] of [
      [880, 0],
      [1174.7, 0.09],
    ] as const) {
      const osc = dingCtx.createOscillator();
      const gain = dingCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t0 + at);
      gain.gain.exponentialRampToValueAtTime(0.05, t0 + at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.22);
      osc.connect(gain).connect(dingCtx.destination);
      osc.start(t0 + at);
      osc.stop(t0 + at + 0.25);
    }
  } catch {
    // Audio unavailable — stay silent.
  }
}

const FILTER_BTN =
  "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors";

const FACT_LABEL =
  "w-[64px] shrink-0 pt-px font-mono text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]";

export function NotificationsWidget({ config, onConfigChange }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

  const notifications = useNotificationStore((s) => s.notifications);
  const hydrated = useNotificationStore((s) => s.hydrated);
  const hydrate = useNotificationStore((s) => s.hydrate);
  const error = useNotificationStore((s) => s.error);
  const retryHydrate = useNotificationStore((s) => s.retryHydrate);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const markRead = useNotificationStore((s) => s.markRead);
  const dismiss = useNotificationStore((s) => s.dismiss);
  const activate = useNotificationActivate();

  const [filter, setFilter] = useState<FilterMode>("all");
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const dingOn = config.ding !== false;

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  // Ding on genuinely new arrivals only — the hydrate burst seeds the ref,
  // and a timestamp guard keeps dismissing the top card from re-dinging on
  // the older item that slides into its place.
  const newestAtRef = useRef<number | null>(null);
  useEffect(() => {
    const newest = notifications[0];
    if (!hydrated || !newest) return;
    const at = Date.parse(newest.created_at);
    if (newestAtRef.current === null) {
      newestAtRef.current = at;
      return;
    }
    if (at > newestAtRef.current) {
      newestAtRef.current = at;
      if (dingOn && !newest.read) playDing();
    }
  }, [notifications, hydrated, dingOn]);

  const unread = unreadCount(notifications);

  const cards = useMemo(() => {
    let items = notifications;
    if (filter === "unread") items = items.filter((n) => !n.read);
    else if (filter !== "all") {
      items = items.filter((n) => categoryOf(n.kind) === filter);
    }
    return groupCards(items);
  }, [notifications, filter]);

  const handleCardClick = (card: Card) => {
    const willExpand = expandedKey !== card.key;
    setExpandedKey(willExpand ? card.key : null);
    if (willExpand) {
      for (const n of card.items) if (!n.read) markRead(n.id);
    }
  };

  if (!hydrated) {
    return <WidgetLoading rows={4} cols={[3, 1]} />;
  }

  if (error && notifications.length === 0) {
    return <WidgetError message={error} onRetry={retryHydrate} />;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden px-1.5 py-1">
      <div className="mb-1.5 flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 px-1">
        <div className="flex min-w-0 flex-1 flex-wrap gap-1">
          {(["all", "unread", ...CATEGORIES] as FilterMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              className={cn(
                FILTER_BTN,
                filter === mode
                  ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                  : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
              )}
              onClick={() => setFilter(mode)}
            >
              {mode}
            </button>
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {unread > 0 ? (
            <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
              {unread} unread
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => onConfigChange({ ding: !dingOn })}
            aria-label={dingOn ? "Mute notification sound" : "Unmute notification sound"}
            title={
              dingOn
                ? "sound on new cards — click to mute"
                : "muted — click to unmute"
            }
            className={cn(
              "rounded p-1 transition-colors",
              dingOn
                ? "text-[var(--fg-dim)] hover:text-foreground"
                : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
          >
            {dingOn ? <Bell className="size-3.5" /> : <BellOff className="size-3.5" />}
          </button>
          <button
            type="button"
            disabled={unread === 0}
            onClick={() => markAllRead()}
            className={cn(
              FILTER_BTN,
              unread === 0
                ? "cursor-not-allowed text-[var(--fg-faint)]"
                : "text-[var(--c-accent)] hover:bg-[var(--c-accent-soft)]",
            )}
          >
            mark all read
          </button>
        </div>
      </div>

      {cards.length === 0 ? (
        <WidgetEmpty message="No notifications" className="p-2" />
      ) : (
        <ul className="min-h-0 flex-1 space-y-1.5 overflow-auto scroll-shadow-y px-0.5 pb-1">
          {cards.map((card) => {
            const newest = card.items[0];
            const count = card.items.length;
            const anyUnread = card.items.some((n) => !n.read);
            const expanded = expandedKey === card.key;
            const symbol = symbolOf(newest);
            const headline = headlineText(newest, symbol);
            const stripe = severityStripeClass(newest.severity);
            const { facts, caveat } = expanded
              ? factLines(newest)
              : { facts: [], caveat: null };
            const preview = count === 1 ? previewText(newest) : null;
            return (
              <li
                key={card.key}
                className={cn(
                  "group rounded-md border border-[var(--border)] bg-[var(--bg-raised)] transition-colors",
                  stripe,
                  expanded
                    ? "border-[var(--border-strong,var(--border))]"
                    : "hover:border-[var(--border-strong,var(--border))]",
                )}
              >
                <div className="flex">
                  {/* div-with-role, not <button>: the headline nests the
                      clickable ticker (itself a button). */}
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => handleCardClick(card)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleCardClick(card);
                      }
                    }}
                    className="min-w-0 flex-1 cursor-pointer px-2.5 py-2 text-left"
                  >
                    {/* micro header: kind word … unread dot · time. The dot
                        lives on the right so text lines all start flush left. */}
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "font-mono text-[length:var(--fs-micro)] lowercase tracking-[0.1em]",
                          kindWordClass(newest.kind),
                        )}
                      >
                        {formatKind(newest.kind)}
                      </span>
                      <span className="ml-auto flex items-center gap-1.5">
                        {anyUnread ? (
                          <span className="size-1.5 shrink-0 rounded-full bg-[var(--c-accent)] shadow-[0_0_6px_var(--c-accent)]" />
                        ) : null}
                        <span className="font-mono text-[length:var(--fs-micro)] tabular-nums text-[var(--fg-faint)]">
                          {relativeTime(newest.created_at)}
                        </span>
                      </span>
                    </div>

                    {/* headline: bold mono ticker + magnitude-accented text */}
                    <p
                      className={cn(
                        "mt-1 text-[length:var(--fs-body)] leading-snug text-foreground",
                        anyUnread ? "font-medium" : "font-normal",
                      )}
                    >
                      {symbol ? (
                        <span
                          className="mr-1.5 font-mono font-bold"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ClickableSymbol
                            symbol={symbol}
                            linked={linked}
                            onActivate={() => publish(symbol)}
                          />
                        </span>
                      ) : null}
                      {highlightMagnitudes(headline)}
                    </p>

                    {/* batch line, or a one-line fact preview on singles */}
                    {count > 1 ? (
                      <p className="mt-1 font-mono text-[length:var(--fs-micro)] text-[var(--c-accent)]">
                        ◱ {count} batched ·{" "}
                        {formatClock(card.items[count - 1].created_at)}–
                        {formatClock(newest.created_at)}
                      </p>
                    ) : !expanded && preview ? (
                      <p className="mt-0.5 line-clamp-1 text-[length:var(--fs-table)] leading-snug text-[var(--fg-dim)]">
                        {highlightMagnitudes(preview)}
                      </p>
                    ) : null}
                  </div>

                  <div className="flex shrink-0 items-start gap-0.5 pt-1.5 pr-1.5">
                    <button
                      type="button"
                      className="inline-flex size-5 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] opacity-0 transition-all group-hover:opacity-100 hover:bg-[var(--bg-hover)] hover:text-[var(--c-accent)]"
                      aria-label="Open in workspace"
                      onClick={(e) => {
                        e.stopPropagation();
                        activate(newest);
                      }}
                    >
                      →
                    </button>
                    <button
                      type="button"
                      className="inline-flex size-5 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] opacity-0 transition-all group-hover:opacity-100 hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                      aria-label={
                        count > 1 ? "Dismiss batch" : "Dismiss notification"
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        for (const n of card.items) dismiss(n.id);
                        if (expandedKey === card.key) setExpandedKey(null);
                      }}
                    >
                      ✕
                    </button>
                  </div>
                </div>

                {/* expanded single: labeled one-fact lines + honesty footer */}
                {expanded && count === 1 && (facts.length > 0 || caveat) ? (
                  <div className="border-t border-[var(--border)] px-2.5 py-2">
                    {facts.map((f, i) => (
                      <div
                        key={i}
                        className={cn(
                          "flex gap-2 py-0.5",
                          i > 0 &&
                            "border-t border-dashed border-[var(--border)]",
                        )}
                      >
                        {f.label ? (
                          <span className={FACT_LABEL}>{f.label}</span>
                        ) : null}
                        <span className="min-w-0 text-[length:var(--fs-table)] leading-relaxed text-[var(--fg-dim)] tabular-nums">
                          {highlightMagnitudes(f.value)}
                        </span>
                      </div>
                    ))}
                    {caveat ? (
                      <p className="mt-1.5 border-t border-[var(--border)] pt-1.5 font-mono text-[length:var(--fs-micro)] text-[var(--c-amber)]">
                        ⚠ {caveat}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {/* expanded batch: member timeline */}
                {expanded && count > 1 ? (
                  <ul className="border-t border-[var(--border)] px-2.5 py-1">
                    {card.items.map((n) => {
                      const mSym = symbolOf(n);
                      return (
                        <li key={n.id}>
                          <button
                            type="button"
                            onClick={() => activate(n)}
                            className="flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left transition-colors hover:bg-[var(--bg-hover)]"
                          >
                            <span className="shrink-0 font-mono text-[length:var(--fs-micro)] tabular-nums text-[var(--fg-faint)]">
                              {formatClock(n.created_at)}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                              {highlightMagnitudes(headlineText(n, mSym))}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
