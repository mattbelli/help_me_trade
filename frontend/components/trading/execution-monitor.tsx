"use client";

import { useNotificationStream } from "@/lib/workspace/use-notification-stream";

export function ExecutionMonitor() {
  useNotificationStream();
  return null;
}
