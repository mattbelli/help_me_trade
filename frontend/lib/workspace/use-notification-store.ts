"use client";

import { create } from "zustand";

import { apiFetch } from "@/lib/api";

const STORAGE_KEY = "hmt:notifications:v1";
const MAX_NOTIFICATIONS = 50;

export type WorkspaceNotification = {
  id: string;
  ruleId?: string;
  ruleName: string;
  action?: unknown;
  source: string;
  message?: string;
  ts: string;
  read: boolean;
};

type PersistedState = {
  notifications: WorkspaceNotification[];
};

type NotificationState = {
  notifications: WorkspaceNotification[];
  hydrated: boolean;
  addNotification: (n: WorkspaceNotification) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  clearAll: () => void;
  hydrate: () => Promise<void>;
};

function actionMessage(action: unknown): string | undefined {
  if (action == null) return undefined;
  if (typeof action === "string") return action;
  if (typeof action === "object" && action !== null) {
    const obj = action as Record<string, unknown>;
    if (typeof obj.message === "string" && obj.message.trim()) {
      return obj.message.trim();
    }
    if (typeof obj.type === "string") {
      return obj.type;
    }
  }
  return undefined;
}

export function notificationLabel(n: WorkspaceNotification): string {
  if (n.message?.trim()) return n.message.trim();
  const fromAction = actionMessage(n.action);
  if (fromAction) return fromAction;
  if (n.source === "signal_scan") return "Signal scan triggered";
  return "Rule triggered";
}

function loadPersisted(): PersistedState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedState;
    if (!Array.isArray(parsed.notifications)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function persist(notifications: WorkspaceNotification[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ notifications }));
  } catch {
    // ignore quota / private mode
  }
}

function mergeServerAlerts(
  local: WorkspaceNotification[],
  server: WorkspaceNotification[],
): WorkspaceNotification[] {
  const byId = new Map(local.map((n) => [n.id, n]));
  for (const item of server) {
    if (!byId.has(item.id)) {
      byId.set(item.id, item);
    }
  }
  return [...byId.values()]
    .sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))
    .slice(0, MAX_NOTIFICATIONS);
}

function maybeOsNotify(n: WorkspaceNotification): void {
  if (typeof document !== "undefined" && document.hasFocus()) return;
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  new Notification(n.ruleName, {
    body: notificationLabel(n),
  });
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  hydrated: false,

  addNotification: (n) => {
    const incoming: WorkspaceNotification = { ...n, read: n.read ?? false };
    set((state) => {
      if (state.notifications.some((item) => item.id === incoming.id)) {
        return state;
      }
      const notifications = [incoming, ...state.notifications].slice(
        0,
        MAX_NOTIFICATIONS,
      );
      persist(notifications);
      return { notifications };
    });
    maybeOsNotify(incoming);
  },

  markRead: (id) => {
    set((state) => {
      const notifications = state.notifications.map((n) =>
        n.id === id ? { ...n, read: true } : n,
      );
      persist(notifications);
      return { notifications };
    });
  },

  markAllRead: () => {
    set((state) => {
      const notifications = state.notifications.map((n) =>
        n.read ? n : { ...n, read: true },
      );
      persist(notifications);
      return { notifications };
    });
  },

  clearAll: () => {
    persist([]);
    set({ notifications: [] });
  },

  hydrate: async () => {
    if (get().hydrated) return;

    const persisted = loadPersisted();
    if (persisted) {
      set({ notifications: persisted.notifications, hydrated: true });
    } else {
      set({ hydrated: true });
    }

    try {
      const res = await apiFetch("/api/alerts/recent");
      if (!res.ok) return;
      const body = (await res.json()) as {
        alerts?: Array<{
          id?: string;
          rule_id?: string;
          rule_name?: string;
          action?: unknown;
          source?: string;
          ts?: string;
        }>;
      };
      const serverItems: WorkspaceNotification[] = (body.alerts ?? [])
        .filter((a) => a.id)
        .map((a) => ({
          id: a.id!,
          ruleId: a.rule_id,
          ruleName: a.rule_name ?? "Alert",
          action: a.action,
          source: a.source ?? "rule",
          message: actionMessage(a.action),
          ts: a.ts ?? new Date().toISOString(),
          read: false,
        }));

      set((state) => {
        const readById = new Map(
          state.notifications.map((n) => [n.id, n.read]),
        );
        const merged = mergeServerAlerts(state.notifications, serverItems).map(
          (n) => ({
            ...n,
            read: readById.get(n.id) ?? n.read,
          }),
        );
        persist(merged);
        return { notifications: merged, hydrated: true };
      });
    } catch {
      // Best-effort server merge.
    }
  },
}));

export function unreadCount(notifications: WorkspaceNotification[]): number {
  return notifications.filter((n) => !n.read).length;
}
