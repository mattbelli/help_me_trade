"use client";

import { useEffect, useState } from "react";

import { TerminalToggle } from "@/components/ui/terminal";
import { fetchChatModels, type ChatModelOption } from "@/lib/ai-models";
import {
  type AiProviderId,
  type AiSettings,
} from "@/lib/ai-settings";
import { cn } from "@/lib/utils";

type Props = {
  providerId: AiProviderId;
  ai: AiSettings;
  onChange: (next: AiSettings) => void;
  className?: string;
  modelsRevision?: number;
};

function isModelEnabled(
  providerId: AiProviderId,
  modelId: string,
  enabledMap: AiSettings["chat_enabled_models"],
  catalog: ChatModelOption[],
): boolean {
  const configured = enabledMap?.[providerId];
  if (!configured || configured.length === 0) {
    return true;
  }
  return configured.includes(modelId);
}

export function ChatModelPickerSettings({
  providerId,
  ai,
  onChange,
  className,
  modelsRevision = 0,
}: Props) {
  const [catalog, setCatalog] = useState<ChatModelOption[]>([]);
  const [source, setSource] = useState<string>("unavailable");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (providerId === "mock") {
      setCatalog([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchChatModels(providerId, { catalog: true })
      .then((data) => {
        if (cancelled) return;
        const models = data.catalog_models ?? data.models ?? [];
        setCatalog(models);
        setSource(data.source ?? "unavailable");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load models");
        setCatalog([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [providerId, modelsRevision]);

  if (providerId === "mock") {
    return null;
  }

  function setModelEnabled(modelId: string, enabled: boolean) {
    const allIds = catalog.map((m) => m.id);
    const current = ai.chat_enabled_models?.[providerId];
    const baseline =
      current && current.length > 0 ? new Set(current) : new Set(allIds);

    if (enabled) {
      baseline.add(modelId);
    } else {
      baseline.delete(modelId);
    }

    const nextIds = allIds.filter((id) => baseline.has(id));
    const nextMap = { ...ai.chat_enabled_models };

    if (nextIds.length === 0 || nextIds.length === allIds.length) {
      delete nextMap[providerId];
    } else {
      nextMap[providerId] = nextIds;
    }

    onChange({ ...ai, chat_enabled_models: nextMap });
  }

  return (
    <div className={cn("space-y-2 rounded-lg border border-border/60 p-3", className)}>
      <div>
        <p className="text-sm font-medium">Chat model picker</p>
        <p className="text-muted-foreground text-xs">
          Models available in the chat composer.
          {source === "live"
            ? " Loaded live from your provider API."
            : " Nothing discovered — check API key, base URL, and provider status."}
        </p>
      </div>

      {loading ? (
        <p className="text-muted-foreground text-xs">Loading models…</p>
      ) : null}
      {error ? (
        <p className="text-destructive text-xs">{error}</p>
      ) : null}

      {!loading && catalog.length === 0 ? (
        <p className="text-muted-foreground text-xs">
          No models discovered. Check API key and base URL above.
        </p>
      ) : null}

      <ul className="max-h-48 space-y-2 overflow-y-auto pr-1">
        {catalog.map((model) => {
          const enabled = isModelEnabled(
            providerId,
            model.id,
            ai.chat_enabled_models,
            catalog,
          );
          return (
            <li
              key={model.id}
              className="flex items-start justify-between gap-3 rounded-md px-1 py-0.5"
            >
              <div className="min-w-0 leading-snug">
                <span className="font-mono text-[11px] text-foreground">
                  {model.label}
                </span>
                <span className="mt-0.5 block font-mono text-[10px] text-[var(--fg-faint)]">
                  {model.id}
                </span>
              </div>
              <TerminalToggle
                checked={enabled}
                onCheckedChange={(checked) =>
                  setModelEnabled(model.id, checked === true)
                }
                aria-label={model.label}
              />
            </li>
          );
        })}
      </ul>
    </div>
  );
}
