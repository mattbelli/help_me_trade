import { Suspense } from "react";

import { PerformancePage } from "@/components/performance/performance-page";

export default function Page() {
  return (
    <Suspense
      fallback={
        <div className="p-8 text-sm text-muted-foreground">
          Loading performance…
        </div>
      }
    >
      <PerformancePage />
    </Suspense>
  );
}
