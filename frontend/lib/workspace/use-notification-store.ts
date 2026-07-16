"use client";

import { create } from "zustand";

import {
  dismissNotification,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationSeverity,
  type ServerNotification,
} from "@/lib/api";

const MAX_NOTIFICATIONS = 100;

export type WorkspaceNotification = {
  id: string;
  kind: string;
  title: string;
  body: string;
  severity: NotificationSeverity;
  action_url?: string | null;
  ref_type?: string | null;
  ref_id?: string | null;
  group_key?: string | null;
  data?: Record<string, unknown> | null;
  created_at: string;
  read: boolean;
  dismissed?: boolean;
};

type NotificationState = {
  notifications: WorkspaceNotification[];
  hydrated: boolean;
  error: string | null;
  addNotification: (n: WorkspaceNotification) => void;
  applyUpdate: (
    id: string,
    patch: { read?: boolean; dismissed?: boolean },
  ) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  dismiss: (id: string) => void;
  clearAll: () => void;
  hydrate: () => Promise<void>;
  retryHydrate: () => Promise<void>;
};

export function mapServerNotification(
  row: ServerNotification,
): WorkspaceNotification {
  return {
    id: row.id,
    kind: row.kind,
    title: row.title,
    body: row.body,
    severity: row.severity,
    action_url: row.action_url,
    ref_type: row.ref_type,
    ref_id: row.ref_id,
    group_key: row.group_key,
    data: row.data,
    created_at: row.created_at,
    read: row.read,
    dismissed: row.dismissed,
  };
}

export function notificationLabel(n: WorkspaceNotification): string {
  return n.body?.trim() || n.title;
}

function maybeOsNotify(n: WorkspaceNotification): void {
  if (typeof document !== "undefined" && document.hasFocus()) return;
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  new Notification(n.title, {
    body: notificationLabel(n),
  });
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  hydrated: false,
  error: null,

  addNotification: (n) => {
    if (n.dismissed) return;
    const incoming: WorkspaceNotification = { ...n, read: n.read ?? false };
    set((state) => {
      if (state.notifications.some((item) => item.id === incoming.id)) {
        return state;
      }
      const notifications = [incoming, ...state.notifications]
        .sort(
          (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
        )
        .slice(0, MAX_NOTIFICATIONS);
      return { notifications };
    });
    maybeOsNotify(incoming);
  },

  applyUpdate: (id, patch) => {
    set((state) => {
      if (patch.dismissed) {
        return {
          notifications: state.notifications.filter((n) => n.id !== id),
        };
      }
      return {
        notifications: state.notifications.map((n) =>
          n.id === id ? { ...n, ...patch } : n,
        ),
      };
    });
  },

  markRead: (id) => {
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, read: true } : n,
      ),
    }));
    void markNotificationRead(id).catch(() => {});
  },

  markAllRead: () => {
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.read ? n : { ...n, read: true },
      ),
    }));
    void markAllNotificationsRead().catch(() => {});
  },

  dismiss: (id) => {
    set((state) => ({
      notifications: state.notifications.filter((n) => n.id !== id),
    }));
    void dismissNotification(id).catch(() => {});
  },

  clearAll: () => {
    set({ notifications: [] });
  },

  hydrate: async () => {
    if (get().hydrated) return;

    try {
      const body = await fetchNotifications({ limit: MAX_NOTIFICATIONS });
      const notifications = body.notifications
        .map(mapServerNotification)
        .filter((n) => !n.dismissed)
        .sort(
          (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
        )
        .slice(0, MAX_NOTIFICATIONS);
      set({ notifications, hydrated: true, error: null });
    } catch (err) {
      set({
        hydrated: true,
        error: err instanceof Error ? err.message : "failed to load",
      });
    }
  },

  retryHydrate: async () => {
    set({ hydrated: false, error: null });
    await get().hydrate();
  },
}));

export function unreadCount(notifications: WorkspaceNotification[]): number {
  return notifications.filter((n) => !n.read).length;
}
