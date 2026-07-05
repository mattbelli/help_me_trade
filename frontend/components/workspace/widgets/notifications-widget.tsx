"use client";

import { useEffect, useMemo, useState } from "react";

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

type FilterMode = "all" | "unread" | `kind:${string}`;

function formatKind(kind: string): string {
  return kind.replace(/_/g, " ");
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

function kindChipClass(kind: string): string {
  if (kind === "proposal" || kind === "execution") {
    return "text-[var(--c-accent)] border-[color-mix(in_srgb,var(--c-accent)_35%,transparent)]";
  }
  if (kind === "price_alert" || kind === "rvol") {
    return "text-[var(--c-amber)] border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)]";
  }
  if (kind === "report_ready") {
    return "text-[var(--c-blue)] border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)]";
  }
  return "text-[var(--fg-dim)] border-[color-mix(in_srgb,var(--fg-dim)_35%,transparent)]";
}

function filterNotifications(
  items: WorkspaceNotification[],
  filter: FilterMode,
): WorkspaceNotification[] {
  if (filter === "all") return items;
  if (filter === "unread") return items.filter((n) => !n.read);
  const kind = filter.slice("kind:".length);
  return items.filter((n) => n.kind === kind);
}

export function NotificationsWidget({}: WidgetProps) {
  const notifications = useNotificationStore((s) => s.notifications);
  const hydrated = useNotificationStore((s) => s.hydrated);
  const hydrate = useNotificationStore((s) => s.hydrate);
  const error = useNotificationStore((s) => s.error);
  const retryHydrate = useNotificationStore((s) => s.retryHydrate);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const dismiss = useNotificationStore((s) => s.dismiss);
  const activate = useNotificationActivate();

  const [filter, setFilter] = useState<FilterMode>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  const unread = unreadCount(notifications);

  const kinds = useMemo(() => {
    const seen = new Set<string>();
    for (const n of notifications) {
      seen.add(n.kind);
    }
    return [...seen].sort((a, b) => a.localeCompare(b));
  }, [notifications]);

  const visible = useMemo(
    () => filterNotifications(notifications, filter),
    [notifications, filter],
  );

  const handleRowClick = (n: WorkspaceNotification) => {
    // Deep-link/focus the target (also marks read) and toggle the body preview.
    activate(n);
    setExpandedId((prev) => (prev === n.id ? null : n.id));
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
          <button
            type="button"
            className={cn(
              "rounded border px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
              filter === "all"
                ? "border-[var(--border)] bg-[var(--bg-hover)] text-foreground"
                : "border-transparent text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
            onClick={() => setFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            className={cn(
              "rounded border px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
              filter === "unread"
                ? "border-[var(--border)] bg-[var(--bg-hover)] text-foreground"
                : "border-transparent text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
            onClick={() => setFilter("unread")}
          >
            Unread
          </button>
          {kinds.map((kind) => {
            const mode = `kind:${kind}` as const;
            return (
              <button
                key={kind}
                type="button"
                className={cn(
                  "rounded border px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
                  filter === mode
                    ? "border-[var(--border)] bg-[var(--bg-hover)] text-foreground"
                    : "border-transparent text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
                )}
                onClick={() => setFilter(mode)}
              >
                {formatKind(kind)}
              </button>
            );
          })}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {unread > 0 ? (
            <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
              {unread} unread
            </span>
          ) : null}
          <button
            type="button"
            disabled={unread === 0}
            onClick={() => markAllRead()}
            className={cn(
              "rounded border px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
              unread === 0
                ? "cursor-not-allowed border-transparent text-[var(--fg-faint)]"
                : "border-transparent text-[var(--c-accent)] hover:bg-[var(--c-accent-soft)]",
            )}
          >
            Mark all read
          </button>
        </div>
      </div>

      {visible.length === 0 ? (
        <WidgetEmpty message="No notifications" className="p-2" />
      ) : (
        <ul className="min-h-0 flex-1 space-y-1 overflow-auto">
          {visible.map((n) => {
            const expanded = expandedId === n.id;
            return (
              <li
                key={n.id}
                className={cn(
                  "group rounded border-l-2 transition-colors",
                  severityAccentClass(n.severity),
                  expanded
                    ? "bg-[var(--bg-hover)]"
                    : "hover:bg-[var(--bg-hover)]",
                )}
              >
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() => handleRowClick(n)}
                    className="min-w-0 flex-1 px-2 py-1.5 text-left"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={cn(
                          "mt-0.5 size-1.5 shrink-0 rounded-full",
                          n.read
                            ? "bg-transparent"
                            : "bg-[var(--c-accent)] shadow-[0_0_6px_var(--c-accent)]",
                        )}
                      />
                      <span
                        className={cn(
                          "rounded border px-1 py-px font-mono text-[length:var(--fs-micro)] uppercase tracking-[0.08em]",
                          kindChipClass(n.kind),
                        )}
                      >
                        {formatKind(n.kind)}
                      </span>
                      <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
                        {relativeTime(n.created_at)}
                      </span>
                    </div>
                    <p
                      className={cn(
                        "mt-1 font-mono text-[length:var(--fs-body)] leading-snug",
                        n.read
                          ? "font-normal text-foreground"
                          : "font-semibold text-foreground",
                      )}
                    >
                      {n.title}
                    </p>
                    {expanded ? (
                      <p className="mt-1.5 whitespace-pre-wrap font-mono text-[length:var(--fs-body)] leading-relaxed text-[var(--fg-dim)]">
                        {n.body}
                      </p>
                    ) : n.body ? (
                      <p className="mt-0.5 line-clamp-2 font-mono text-[length:var(--fs-table)] leading-snug text-[var(--fg-dim)]">
                        {n.body}
                      </p>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    className="mt-1 inline-flex size-5 shrink-0 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] opacity-0 transition-all group-hover:opacity-100 hover:bg-[var(--bg-hover)] hover:text-[var(--c-accent)]"
                    aria-label="Open in workspace"
                    onClick={(e) => {
                      e.stopPropagation();
                      activate(n);
                    }}
                  >
                    →
                  </button>
                  <button
                    type="button"
                    className="mt-1 inline-flex size-5 shrink-0 items-center justify-center rounded font-mono text-[length:var(--fs-body)] text-[var(--fg-faint)] opacity-0 transition-all group-hover:opacity-100 hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                    aria-label="Dismiss notification"
                    onClick={(e) => {
                      e.stopPropagation();
                      dismiss(n.id);
                      if (expandedId === n.id) setExpandedId(null);
                    }}
                  >
                    ✕
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
