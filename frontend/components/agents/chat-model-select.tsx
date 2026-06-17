"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Cpu } from "lucide-react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  CUSTOM_MODEL_VALUE,
  fetchChatModels,
  readStoredChatModel,
  resolveInitialChatModel,
  writeStoredChatModel,
  type ChatModelCatalog,
} from "@/lib/ai-models";
import { subscribeSettingsSaved } from "@/lib/settings-events";
import { cn } from "@/lib/utils";

type Props = {
  provider: string;
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
  className?: string;
  /** When set, prefer this model over localStorage on catalog load. */
  preferredModel?: string;
};

export function ChatModelSelect({
  provider,
  value,
  onChange,
  disabled = false,
  className,
  preferredModel,
}: Props) {
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [customMode, setCustomMode] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const onChangeRef = useRef(onChange);
  const valueRef = useRef(value);
  const preferredModelRef = useRef(preferredModel);
  onChangeRef.current = onChange;
  valueRef.current = value;
  preferredModelRef.current = preferredModel;

  useEffect(() => subscribeSettingsSaved(() => setReloadToken((n) => n + 1)), []);

  const loadCatalog = useCallback(() => {
    if (!provider || provider === "mock") {
      setCatalog(null);
      setLoading(false);
      return () => {};
    }

    let cancelled = false;
    setLoading(true);

    void fetchChatModels(provider)
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);

        const currentValue = valueRef.current?.trim();
        const valueInList =
          !!currentValue && data.models.some((m) => m.id === currentValue);

        if (valueInList) {
          setCustomMode(false);
          setCustomValue("");
          return;
        }

        const stored = readStoredChatModel(provider);
        const preferred = preferredModelRef.current?.trim();
        const resolved = resolveInitialChatModel(
          data,
          preferred && data.models.some((m) => m.id === preferred)
            ? preferred
            : stored,
        );
        if (resolved.isCustom) {
          setCustomMode(true);
          setCustomValue(resolved.model);
        } else {
          setCustomMode(false);
          setCustomValue("");
        }
        if (resolved.model) {
          onChangeRef.current(resolved.model);
        }
      })
      .catch(() => {
        if (cancelled) return;
        const fallback = preferredModelRef.current?.trim() || valueRef.current?.trim();
        setCatalog({
          provider,
          default_model: fallback ?? "",
          models: fallback ? [{ id: fallback, label: fallback }] : [],
          supports_custom: true,
          source: "unavailable",
        });
        if (fallback) {
          onChangeRef.current(fallback);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [provider]);

  useEffect(() => loadCatalog(), [loadCatalog, reloadToken]);

  const models = useMemo(() => {
    const base = catalog?.models ?? [];
    if (!value) return base;
    if (base.some((m) => m.id === value)) return base;
    return [...base, { id: value, label: value }];
  }, [catalog, value]);

  const selectValue = customMode ? CUSTOM_MODEL_VALUE : value || undefined;

  function handleSelectChange(next: string | null) {
    if (!next) return;
    if (next === CUSTOM_MODEL_VALUE) {
      setCustomMode(true);
      const seed = customValue.trim() || value || catalog?.default_model || "";
      setCustomValue(seed);
      if (seed) {
        onChange(seed);
        writeStoredChatModel(provider, seed);
      }
      return;
    }
    setCustomMode(false);
    setCustomValue("");
    onChange(next);
    writeStoredChatModel(provider, next);
  }

  function handleCustomInput(next: string) {
    setCustomValue(next);
    const trimmed = next.trim();
    if (trimmed) {
      onChange(trimmed);
      writeStoredChatModel(provider, trimmed);
    }
  }

  if (!provider || provider === "mock") {
    return null;
  }

  return (
    <div className={cn("flex min-w-0 items-center gap-2", className)}>
      <Select
        value={selectValue}
        onValueChange={handleSelectChange}
        disabled={disabled || loading}
      >
        <SelectTrigger
          size="sm"
          className="h-7 max-w-[220px] gap-1.5 border-border/70 bg-background/80 text-xs"
        >
          <Cpu className="size-3.5 shrink-0 text-muted-foreground" />
          <SelectValue
            placeholder={loading ? "Loading…" : models.length ? "Model" : "No models"}
          />
        </SelectTrigger>
        <SelectContent>
          {models.map((model) => (
            <SelectItem key={model.id} value={model.id} className="text-xs">
              {model.label}
            </SelectItem>
          ))}
          {catalog?.supports_custom !== false ? (
            <SelectItem value={CUSTOM_MODEL_VALUE} className="text-xs">
              Custom model…
            </SelectItem>
          ) : null}
        </SelectContent>
      </Select>

      {customMode ? (
        <Input
          value={customValue}
          onChange={(e) => handleCustomInput(e.target.value)}
          placeholder="model-id"
          disabled={disabled}
          className="h-7 max-w-[180px] text-xs"
        />
      ) : null}
    </div>
  );
}
