"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

import { CompanyInfoModal } from "@/components/workspace/company-info-modal";

type TickerInfoContextValue = {
  openTickerInfo: (symbol: string) => void;
};

// Default to a no-op so useTickerInfo() never throws when a ClickableSymbol is
// rendered outside a provider (e.g. in an isolated test / storybook).
const TickerInfoContext = createContext<TickerInfoContextValue>({
  openTickerInfo: () => {},
});

export function useTickerInfo(): TickerInfoContextValue {
  return useContext(TickerInfoContext);
}

export function TickerInfoProvider({ children }: { children: ReactNode }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  return (
    <TickerInfoContext.Provider value={{ openTickerInfo: setSymbol }}>
      {children}
      <CompanyInfoModal
        symbol={symbol}
        open={symbol != null}
        onClose={() => setSymbol(null)}
        linked={false}
        onPublish={() => {}}
      />
    </TickerInfoContext.Provider>
  );
}
