"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FloatingPanel } from "@/components/ui/floating-panel";
import {
  FactsSection,
  FundamentalsSection,
  HeaderSection,
  NewsSection,
} from "@/components/workspace/company-info-sections";
import { fetchTickerInfo, type TickerInfo } from "@/lib/ticker-info-api";
import { useIsMobile } from "@/lib/workspace/use-is-mobile";

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
  const isMobile = useIsMobile();

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

  const body = loading ? (
    <p className="text-[length:var(--fs-body)] text-[var(--fg-dim)]">Loading…</p>
  ) : error ? (
    <p className="text-[length:var(--fs-body)] text-[var(--c-red)]">{error}</p>
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
      <FundamentalsSection
        profile={data.profile}
        metrics={data.metrics}
        shortInterest={data.short_interest}
        price={data.facts.quote?.price}
      />
      <FactsSection facts={data.facts} />
      <NewsSection news={data.news} />
    </div>
  ) : null;

  // Desktop: float around the cockpit with magnet snap. Mobile: unchanged
  // centered Dialog.
  if (!isMobile) {
    return (
      <FloatingPanel
        open={open}
        onClose={onClose}
        initialWidth={720}
        initialHeight={680}
        ariaLabel={symbol ?? "Company"}
        // Centered card: the height cap sits short of the viewport so the
        // panel never stretches into a full-height column glued to the bottom
        // strip (the body scrolls instead). Tall enough to show the full
        // fundamentals table plus the start of the news list.
        className="w-[min(720px,92vw)] max-h-[min(760px,85vh)]"
        contentClassName="overflow-y-auto p-6"
        header={
          <span className="font-mono text-[length:var(--fs-title)] leading-snug">
            {symbol ?? "Company"}
          </span>
        }
      >
        {body}
      </FloatingPanel>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] w-full max-w-lg overflow-y-auto border border-[var(--border)] bg-[var(--panel)] font-mono text-foreground sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono text-[length:var(--fs-title)]">
            {symbol ?? "Company"}
          </DialogTitle>
        </DialogHeader>

        {body}
      </DialogContent>
    </Dialog>
  );
}
