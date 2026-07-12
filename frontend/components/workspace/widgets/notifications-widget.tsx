"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { NotificationSeverity } from "@/lib/api";
import { relativeTime } from "@/lib/relative-time";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
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
// kind (a dozen kinds wrapped into an illegible chrome row). Unknown kinds
// land in "system" so nothing ever disappears from the inbox.
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

function severityAccentClass(severity: NotificationSeverity): string {
  switch (severity) {
    case "success":
      return "border-l-[var(--c-green)]";
    case "warning":
      return "border-l-[var(--c-amber)]";
    case "critical":
      return "border-l-[var(--c-red)]";
    default:
      return "border-l-[var(--border)]";
  }
}

// ---------------------------------------------------------------------------
// Card grouping — correlated fires (same group_key, e.g. five AAPL sweeps in
// three minutes) collapse into one card with a count. An item joins while it
// is within the window of the group's oldest member, so a steady drip chains
// into a single card instead of five near-identical rows.
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

export function NotificationsWidget({ config, onConfigChange }: WidgetProps) {
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
    if (card.items.length === 1) {
      activate(card.items[0]);
      return;
    }
    // Expanding a batch counts as reading it.
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
            title={dingOn ? "sound on new cards — click to mute" : "muted — click to unmute"}
            className={cn(
              FILTER_BTN,
              dingOn
                ? "text-[var(--fg-dim)] hover:text-foreground"
                : "text-[var(--fg-faint)] line-through hover:text-[var(--fg-dim)]",
            )}
          >
            ding
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
        <ul className="min-h-0 flex-1 space-y-1 overflow-auto scroll-shadow-y">
          {cards.map((card) => {
            const newest = card.items[0];
            const count = card.items.length;
            const anyUnread = card.items.some((n) => !n.read);
            const expanded = expandedKey === card.key;
            return (
              <li
                key={card.key}
                className={cn(
                  "group rounded border-l-2 transition-colors",
                  severityAccentClass(newest.severity),
                  expanded
                    ? "bg-[var(--bg-hover)]"
                    : "hover:bg-[var(--bg-hover)]",
                )}
              >
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() => handleCardClick(card)}
                    className="min-w-0 flex-1 px-2 py-1.5 text-left"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={cn(
                          "mt-0.5 size-1.5 shrink-0 rounded-full",
                          anyUnread
                            ? "bg-[var(--c-accent)] shadow-[0_0_6px_var(--c-accent)]"
                            : "bg-transparent",
                        )}
                      />
                      <span
                        className={cn(
                          "font-mono text-[length:var(--fs-micro)] lowercase tracking-[0.08em]",
                          kindWordClass(newest.kind),
                        )}
                      >
                        {formatKind(newest.kind)}
                      </span>
                      {count > 1 ? (
                        <span className="font-mono text-[length:var(--fs-micro)] tabular-nums text-[var(--fg-dim)]">
                          ×{count}
                        </span>
                      ) : null}
                      <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
                        {relativeTime(newest.created_at)}
                      </span>
                    </div>
                    <p
                      className={cn(
                        "mt-1 font-mono text-[length:var(--fs-body)] leading-snug text-foreground",
                        anyUnread ? "font-semibold" : "font-normal",
                      )}
                    >
                      {newest.title}
                    </p>
                    {count === 1 ? (
                      expanded ? (
                        <p className="mt-1.5 whitespace-pre-wrap font-mono text-[length:var(--fs-body)] leading-relaxed text-[var(--fg-dim)]">
                          {newest.body}
                        </p>
                      ) : newest.body ? (
                        <p className="mt-0.5 line-clamp-2 font-mono text-[length:var(--fs-table)] leading-snug text-[var(--fg-dim)]">
                          {newest.body}
                        </p>
                      ) : null
                    ) : !expanded ? (
                      <p className="mt-0.5 font-mono text-[length:var(--fs-table)] leading-snug text-[var(--fg-faint)]">
                        {count} correlated · {relativeTime(card.items[count - 1].created_at)} –{" "}
                        {relativeTime(newest.created_at)}
                      </p>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    className="mt-1 inline-flex size-5 shrink-0 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] opacity-0 transition-all group-hover:opacity-100 hover:bg-[var(--bg-hover)] hover:text-[var(--c-accent)]"
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
                    className="mt-1 inline-flex size-5 shrink-0 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] opacity-0 transition-all group-hover:opacity-100 hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                    aria-label={count > 1 ? "Dismiss batch" : "Dismiss notification"}
                    onClick={(e) => {
                      e.stopPropagation();
                      for (const n of card.items) dismiss(n.id);
                      if (expandedKey === card.key) setExpandedKey(null);
                    }}
                  >
                    ✕
                  </button>
                </div>
                {expanded && count > 1 ? (
                  <ul className="border-t border-[var(--border)] px-2 py-1">
                    {card.items.map((n) => (
                      <li key={n.id}>
                        <button
                          type="button"
                          onClick={() => activate(n)}
                          className="flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left transition-colors hover:bg-[var(--bg-hover)]"
                        >
                          <span className="shrink-0 font-mono text-[length:var(--fs-micro)] tabular-nums text-[var(--fg-faint)]">
                            {relativeTime(n.created_at)}
                          </span>
                          <span className="min-w-0 flex-1 truncate font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                            {n.title}
                          </span>
                        </button>
                      </li>
                    ))}
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
