import { Suspense } from "react";

import { OrderFlowWidgetLayout } from "@/components/orderflow/orderflow-widget-layout";

export default function OrderFlowPage() {
  return (
    <Suspense
      fallback={
        <div
          className="flex min-h-screen items-center justify-center font-mono text-sm"
          style={{ background: "var(--background)", color: "var(--fg-dim)" }}
        >
          Loading order flow…
        </div>
      }
    >
      <OrderFlowWidgetLayout />
    </Suspense>
  );
}
