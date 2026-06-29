"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, X } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  notificationLabel,
  type WorkspaceNotification,
  useNotificationStore,
} from "@/lib/workspace/use-notification-store";

const AUTO_DISMISS_MS = 6_000;
const MAX_BANNERS = 3;

type ActiveBanner = WorkspaceNotification & { key: string };

export function NotificationBanner() {
  const markRead = useNotificationStore((s) => s.markRead);
  const [active, setActive] = useState<ActiveBanner[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(
    new Map(),
  );

  const dismiss = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setActive((prev) => prev.filter((b) => b.id !== id));
  }, []);

  const scheduleDismiss = useCallback(
    (id: string) => {
      const existing = timersRef.current.get(id);
      if (existing) clearTimeout(existing);
      const timer = setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
      timersRef.current.set(id, timer);
    },
    [dismiss],
  );

  useEffect(() => {
    const known = new Set(
      useNotificationStore.getState().notifications.map((n) => n.id),
    );

    const unsub = useNotificationStore.subscribe((state) => {
      for (const n of state.notifications) {
        if (known.has(n.id)) continue;
        known.add(n.id);
        if (n.read) continue;

        setActive((prev) => {
          const next = [
            { ...n, key: n.id },
            ...prev.filter((b) => b.id !== n.id),
          ];
          return next.slice(0, MAX_BANNERS);
        });
        scheduleDismiss(n.id);
      }
    });

    return unsub;
  }, [scheduleDismiss]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
      timers.clear();
    };
  }, []);

  const handleClick = (banner: ActiveBanner) => {
    markRead(banner.id);
    dismiss(banner.id);
  };

  return (
    <div className="pointer-events-none fixed top-14 right-4 z-[70] flex w-[min(100vw-2rem,22rem)] flex-col gap-2">
      <AnimatePresence mode="popLayout">
        {active.map((banner) => (
          <motion.div
            key={banner.key}
            layout
            initial={{ opacity: 0, x: 48 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 48 }}
            transition={{ type: "spring", stiffness: 420, damping: 32 }}
            className="pointer-events-auto overflow-hidden rounded-lg border border-[color-mix(in_srgb,var(--c-amber)_45%,transparent)] bg-[var(--panel)] shadow-[var(--panel-shadow)]"
          >
            <div className="flex items-start gap-2 px-3 py-2.5">
              <button
                type="button"
                onClick={() => handleClick(banner)}
                className="flex min-w-0 flex-1 items-start gap-2 text-left"
              >
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-[var(--c-amber)]" />
                <span className="min-w-0 font-mono text-[11px]">
                  <span className="block truncate font-semibold text-foreground">
                    {banner.title}
                  </span>
                  <span className="mt-0.5 block truncate text-[var(--fg-dim)]">
                    {notificationLabel(banner)}
                  </span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => {
                  markRead(banner.id);
                  dismiss(banner.id);
                }}
                className={cn(
                  "shrink-0 rounded p-0.5 text-[var(--fg-faint)] transition-colors",
                  "hover:bg-[var(--bg-hover)] hover:text-foreground",
                )}
                aria-label="Dismiss notification"
              >
                <X className="size-3.5" />
              </button>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
