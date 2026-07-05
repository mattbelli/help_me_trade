"use client";

import { PerformancePage } from "@/components/performance/performance-page";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

export function PerformanceWidget({}: WidgetProps) {
  return (
    <div className="h-full overflow-auto px-1.5 py-1 scroll-shadow-y">
      <PerformancePage />
    </div>
  );
}
