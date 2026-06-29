"use client";

import { useEffect } from "react";
import { Bell } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import {
  notificationLabel,
  unreadCount,
  useNotificationStore,
} from "@/lib/workspace/use-notification-store";

function formatRelativeTime(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms)) return "";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

export function NotificationBell() {
  const notifications = useNotificationStore((s) => s.notifications);
  const hydrated = useNotificationStore((s) => s.hydrated);
  const hydrate = useNotificationStore((s) => s.hydrate);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);

  const unread = unreadCount(notifications);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="relative inline-flex cursor-pointer items-center rounded p-1 text-[var(--fg-faint)] outline-none transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground focus-visible:ring-2 focus-visible:ring-[var(--c-accent)]"
        aria-label={
          unread > 0
            ? `Notifications, ${unread} unread`
            : "Notifications"
        }
      >
        <Bell className="size-3.5" />
        {unread > 0 ? (
          <span
            className="absolute -top-0.5 -right-0.5 grid min-w-[14px] place-items-center rounded-full px-0.5 text-[9px] font-bold leading-none text-[var(--c-on-accent)]"
            style={{ backgroundColor: "var(--c-red)" }}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-80 border-[var(--border)] bg-[var(--panel)] p-0 font-mono text-[11px] text-foreground shadow-[var(--panel-shadow)]"
      >
        <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2">
          <span className="font-sans text-[12px] font-semibold tracking-tight">
            Notifications
          </span>
          <button
            type="button"
            disabled={unread === 0}
            onClick={() => markAllRead()}
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] transition-colors",
              unread === 0
                ? "cursor-not-allowed text-[var(--fg-faint)]"
                : "text-[var(--c-accent)] hover:bg-[var(--c-accent-soft)]",
            )}
          >
            Mark all read
          </button>
        </div>

        <div className="max-h-80 overflow-y-auto">
          {!hydrated ? (
            <p className="px-3 py-4 text-[var(--fg-dim)]">Loading…</p>
          ) : notifications.length === 0 ? (
            <p className="px-3 py-6 text-center text-[var(--fg-dim)]">
              No alerts yet — armed rules will show here.
            </p>
          ) : (
            <ul>
              {notifications.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    onClick={() => markRead(n.id)}
                    className="flex w-full gap-2 border-b border-[var(--border)] px-3 py-2.5 text-left transition-colors last:border-b-0 hover:bg-[var(--bg-hover)]"
                  >
                    <span
                      className={cn(
                        "mt-1.5 size-1.5 shrink-0 rounded-full",
                        n.read
                          ? "bg-transparent"
                          : "bg-[var(--c-accent)] shadow-[0_0_6px_var(--c-accent)]",
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-semibold text-foreground">
                        {n.title}
                      </span>
                      <span className="mt-0.5 block truncate text-[var(--fg-dim)]">
                        {notificationLabel(n)}
                      </span>
                      <span className="mt-1 block text-[9.5px] text-[var(--fg-faint)]">
                        {formatRelativeTime(n.created_at)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
