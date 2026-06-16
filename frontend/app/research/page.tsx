import { Suspense } from "react";

import { ResearchPage } from "@/components/research/research-page";

export default function Page() {
  return (
    <Suspense
      fallback={
        <div className="p-8 text-sm text-muted-foreground">Loading research…</div>
      }
    >
      <ResearchPage />
    </Suspense>
  );
}
