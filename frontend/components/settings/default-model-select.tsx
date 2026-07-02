"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TerminalField } from "@/components/ui/terminal";
import {
  defaultLlmKey,
  fetchDefaultModelOptions,
  parseDefaultLlmKey,
} from "@/lib/ai-models";
import {
  AI_PROVIDER_OPTIONS,
  getDefaultLlm,
  isProviderId,
  setDefaultLlm,
  type AiProviderId,
  type AiSettings,
} from "@/lib/ai-settings";

type Props = {
  ai: AiSettings;
  onChange: (next: AiSettings) => void;
  modelsRevision?: number;
};

export function DefaultModelSelect({
  ai,
  onChange,
  modelsRevision = 0,
}: Props) {
  const [options, setOptions] = useState<
    Array<{
      provider: string;
      model: string;
      label: string;
      provider_label: string;
    }>
  >([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const aiRef = useRef(ai);
  const onChangeRef = useRef(onChange);
  aiRef.current = ai;
  onChangeRef.current = onChange;

  const loadOptions = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    void fetchDefaultModelOptions()
      .then((data) => {
        if (cancelled) return;
        setOptions(data.options);
        setError(null);

        const current = getDefaultLlm(aiRef.current);
        const currentKey = defaultLlmKey(current.provider, current.model);
        const hasCurrent = data.options.some(
          (opt) => defaultLlmKey(opt.provider, opt.model) === currentKey,
        );
        const serverDefault = data.default_llm;
        const shouldAdopt =
          serverDefault?.provider &&
          serverDefault?.model &&
          isProviderId(serverDefault.provider) &&
          serverDefault.provider !== "mock" &&
          (!current.model || !hasCurrent);
        if (shouldAdopt) {
          onChangeRef.current(
            setDefaultLlm(aiRef.current, {
              provider: serverDefault.provider as AiProviderId,
              model: serverDefault.model,
            }),
          );
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load models");
        setOptions([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return loadOptions();
  }, [loadOptions, modelsRevision]);

  const current = getDefaultLlm(ai);
  const selectValue =
    current.model && current.provider !== "mock"
      ? defaultLlmKey(current.provider, current.model)
      : undefined;

  const displayOptions = useMemo(() => {
    if (!selectValue) return options;
    if (options.some((opt) => defaultLlmKey(opt.provider, opt.model) === selectValue)) {
      return options;
    }
    const providerLabel =
      AI_PROVIDER_OPTIONS.find((p) => p.id === current.provider)?.label ??
      current.provider;
    return [
      ...options,
      {
        provider: current.provider,
        model: current.model,
        label: `${current.model} (save to refresh list)`,
        provider_label: providerLabel,
      },
    ];
  }, [options, selectValue, current.provider, current.model]);

  const grouped = useMemo(() => {
    const map = new Map<string, typeof displayOptions>();
    for (const opt of displayOptions) {
      const list = map.get(opt.provider_label) ?? [];
      list.push(opt);
      map.set(opt.provider_label, list);
    }
    return [...map.entries()];
  }, [displayOptions]);

  function handleChange(value: string | null) {
    if (!value) return;
    const parsed = parseDefaultLlmKey(value);
    if (!parsed || !isProviderId(parsed.provider) || parsed.provider === "mock") {
      return;
    }
    onChange(
      setDefaultLlm(ai, {
        provider: parsed.provider as AiProviderId,
        model: parsed.model,
      }),
    );
  }

  return (
    <div className="space-y-2">
      <TerminalField label="Default model" htmlFor="default-llm-model">
      <Select
        value={selectValue}
        onValueChange={handleChange}
        disabled={loading && displayOptions.length === 0}
      >
        <SelectTrigger
          id="default-llm-model"
          className="h-8 w-full bg-[var(--bg-inset)] border-[var(--border)] font-mono text-[11px]"
        >
          <SelectValue
            placeholder={
              loading
                ? "Loading models…"
                : displayOptions.length === 0
                  ? "No models found — add an API key and save"
                  : "Select a model"
            }
          />
        </SelectTrigger>
        <SelectContent className="max-h-72">
          {grouped.map(([providerLabel, items]) => (
            <SelectGroup key={providerLabel}>
              <SelectLabel>{providerLabel}</SelectLabel>
              {items.map((item) => (
                <SelectItem
                  key={defaultLlmKey(item.provider, item.model)}
                  value={defaultLlmKey(item.provider, item.model)}
                >
                  {item.label}
                </SelectItem>
              ))}
            </SelectGroup>
          ))}
        </SelectContent>
      </Select>
      </TerminalField>
      <p className="text-muted-foreground text-xs">
        Used for chat, insights, research, and reflection. Save settings after
        adding API keys so models can be discovered.
      </p>
      {error ? <p className="text-destructive text-xs">{error}</p> : null}
    </div>
  );
}
