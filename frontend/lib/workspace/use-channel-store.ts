"use client";

import { create } from "zustand";

import type { ChannelId } from "./channels";

type ChannelState = {
  symbols: Record<ChannelId, string | undefined>;
  setChannelSymbol: (channel: ChannelId, symbol: string) => void;
};

const EMPTY_SYMBOLS: Record<ChannelId, string | undefined> = {
  none: undefined,
  "1": undefined,
  "2": undefined,
  "3": undefined,
  "4": undefined,
  "5": undefined,
  "6": undefined,
};

export const useChannelStore = create<ChannelState>((set) => ({
  symbols: { ...EMPTY_SYMBOLS },
  setChannelSymbol: (channel, symbol) => {
    if (channel === "none") return;
    const normalized = symbol.trim().toUpperCase();
    if (!normalized) return;
    set((state) => ({
      symbols: { ...state.symbols, [channel]: normalized },
    }));
  },
}));

export function useChannelSymbol(channel: ChannelId): string | undefined {
  return useChannelStore((s) =>
    channel === "none" ? undefined : s.symbols[channel],
  );
}
