"use client";

import { Suspense } from "react";

import { ResearchPage } from "@/components/research/research-page";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

export function ResearchWidget({}: WidgetProps) {
  return (
    <div className="h-full overflow-auto px-1.5 py-1">
      <Suspense
        fallback={
          <div className="p-4 text-sm text-muted-foreground">Loading research…</div>
        }
      >
        <ResearchPage />
      </Suspense>
    </div>
  );
}
