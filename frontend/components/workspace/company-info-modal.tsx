"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  FactsSection,
  FundamentalsSection,
  HeaderSection,
  NewsSection,
} from "@/components/workspace/company-info-sections";
import { fetchTickerInfo, type TickerInfo } from "@/lib/ticker-info-api";

export function CompanyInfoModal({
  symbol,
  open,
  onClose,
  linked,
  onPublish,
}: {
  symbol: string | null;
  open: boolean;
  onClose: () => void;
  linked: boolean;
  onPublish: (symbol: string) => void;
}) {
  const [data, setData] = useState<TickerInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !symbol) return;
    let cancelled = false;
    setData(null);
    setError(null);
    setLoading(true);
    fetchTickerInfo(symbol)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load company info");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, symbol]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] w-full max-w-lg overflow-y-auto border border-[var(--border)] bg-[var(--panel)] font-mono text-foreground sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono text-[13px]">
            {symbol ?? "Company"}
          </DialogTitle>
        </DialogHeader>

        {loading ? (
          <p className="text-[11px] text-[var(--fg-dim)]">Loading…</p>
        ) : error ? (
          <p className="text-[11px] text-[var(--c-red)]">{error}</p>
        ) : data ? (
          <div className="flex flex-col gap-4">
            <HeaderSection
              symbol={data.symbol}
              profile={data.profile}
              metrics={data.metrics}
              facts={data.facts}
              linked={linked}
              onPublish={() => onPublish(data.symbol)}
            />
            <FundamentalsSection profile={data.profile} metrics={data.metrics} />
            <FactsSection facts={data.facts} />
            <NewsSection news={data.news} />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
