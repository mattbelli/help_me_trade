"use client";

import { useCallback } from "react";

import { useWorkspaceToast } from "@/components/workspace/workspace-toast";

import type { ChannelId } from "./channels";
import { useChannelStore } from "./use-channel-store";

export function usePublishSymbol(channel: ChannelId) {
  const setChannelSymbol = useChannelStore((s) => s.setChannelSymbol);
  const { showToast } = useWorkspaceToast();
  const linked = channel !== "none";
  const publish = useCallback(
    (symbol: string) => {
      if (!linked) return;
      const sym = symbol.trim().toUpperCase();
      if (!sym) return;
      setChannelSymbol(channel, sym);
      showToast(`→ ${sym} · Ch ${channel}`);
    },
    [channel, linked, setChannelSymbol, showToast],
  );
  return { publish, linked };
}
