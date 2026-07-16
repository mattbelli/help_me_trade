"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { useWorkspaceToast } from "@/components/workspace/workspace-toast";
import { notificationTarget } from "./notification-target";
import {
  useNotificationStore,
  type WorkspaceNotification,
} from "./use-notification-store";
import { useWorkspaceStore } from "./use-workspace-store";

export function useNotificationActivate() {
  const markRead = useNotificationStore((s) => s.markRead);
  const focusWidget = useWorkspaceStore((s) => s.focusWidget);
  const focusSymbol = useWorkspaceStore((s) => s.focusSymbol);
  const { showToast } = useWorkspaceToast();
  const router = useRouter();

  return useCallback(
    (n: WorkspaceNotification) => {
      markRead(n.id);
      const t = notificationTarget(n);
      if (!t) return;
      if ("route" in t) {
        router.push(t.route);
        return;
      }
      if (t.widgetType === "chart" && t.symbol) {
        if (!focusSymbol(t.symbol))
          showToast(`Add a Chart widget to view ${t.symbol}`);
      } else {
        if (!focusWidget(t.widgetType))
          showToast(
            `Open the ${t.widgetType.replace(/-/g, " ")} widget to view`,
          );
      }
    },
    [markRead, focusWidget, focusSymbol, showToast, router],
  );
}
