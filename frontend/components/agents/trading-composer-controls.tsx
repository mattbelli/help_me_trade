"use client";

import { useCallback, useEffect } from "react";

import { ChatModelSelect } from "@/components/agents/chat-model-select";
import { API_BASE, apiFetch } from "@/lib/api";
import { useChatSettings } from "@/lib/chat-settings-context";
import { subscribeSettingsSaved } from "@/lib/settings-events";
import { cn } from "@/lib/utils";

export function TradingComposerControls({ className }: { className?: string }) {
  const { settings, setSettings } = useChatSettings();

  const syncDefaultLlm = useCallback(() => {
    apiFetch(`${API_BASE}/api/ai/providers`, { cache: "no-store" })
      .then((res) => res.json())
      .then((data) => {
        const block = data.default_llm;
        const provider = block?.provider ?? "";
        const model = block?.model ?? "";
        if (!provider || provider === "mock") return;
        setSettings((prev) =>
          prev.provider === provider && prev.model === model
            ? prev
            : { ...prev, provider, model },
        );
      })
      .catch(() => {});
  }, [setSettings]);

  useEffect(() => {
    syncDefaultLlm();
    return subscribeSettingsSaved(syncDefaultLlm);
  }, [syncDefaultLlm]);

  // Tools + auto-inject are no longer toggled in chat — all tools are always on
  // (see DEFAULT_CHAT_SETTINGS); manage anything tool-related from Settings.
  return (
    <div
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-1.5 ps-0.5",
        className,
      )}
    >
      <ChatModelSelect
        key={settings.provider}
        provider={settings.provider}
        value={settings.model}
        preferredModel={settings.model}
        onChange={(model) => setSettings((prev) => ({ ...prev, model }))}
        className="shrink-0"
      />
    </div>
  );
}
