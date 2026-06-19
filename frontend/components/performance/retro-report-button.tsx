"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { ClipboardList, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  runTradeRetrospective,
  type PerformancePeriod,
} from "@/lib/performance-api";

type RetroReportButtonProps = {
  period: PerformancePeriod;
  accountId?: string;
};

export function RetroReportButton({ period, accountId }: RetroReportButtonProps) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await runTradeRetrospective({
        period,
        account_id: accountId !== "all" ? accountId : undefined,
      });
      router.push(`/research?id=${result.artifact_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Trade review failed");
    } finally {
      setLoading(false);
    }
  }, [accountId, period, router]);

  return (
    <div className="flex flex-col items-end gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={loading}
        onClick={() => void handleClick()}
        className="shrink-0"
      >
        {loading ? (
          <Loader2 className="mr-2 size-4 animate-spin" />
        ) : (
          <ClipboardList className="mr-2 size-4" />
        )}
        Run Trade Review
      </Button>
      {error ? (
        <p
          role="alert"
          className="max-w-xs rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive"
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}
