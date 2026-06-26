"use client";

import { useExecutionMonitor } from "@/lib/use-execution-monitor";

export function ExecutionMonitor() {
  useExecutionMonitor();
  return null;
}
