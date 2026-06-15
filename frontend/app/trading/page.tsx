import { Suspense } from "react";

import { TradingPage } from "@/components/trading/trading-page";
import { Skeleton } from "@/components/ui/skeleton";

export default function TradingRoute() {
  return (
    <Suspense fallback={<Skeleton className="h-[520px] w-full rounded-lg" />}>
      <TradingPage />
    </Suspense>
  );
}
