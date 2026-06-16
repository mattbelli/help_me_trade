"use client";

import { useEffect, useState } from "react";
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
import { cn } from "@/lib/utils";

type Props = {
  provider: string;
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
  className?: string;
};

export function ChatModelSelect({
  provider,
  value,
  onChange,
  disabled = false,
  className,
}: Props) {
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [customMode, setCustomMode] = useState(false);
  const [customValue, setCustomValue] = useState("");

  useEffect(() => {
    let cancelled = false;
    setCatalog(null);

    void fetchChatModels(provider)
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        const stored = readStoredChatModel(provider);
        const resolved = resolveInitialChatModel(data, stored);
        if (resolved.isCustom) {
          setCustomMode(true);
          setCustomValue(resolved.model);
        } else {
          setCustomMode(false);
          setCustomValue("");
        }
        onChange(resolved.model);
      })
      .catch(() => {
        if (cancelled) return;
        setCatalog({
          provider,
          default_model: "mock",
          models: [{ id: "mock", label: "Mock (offline)" }],
          supports_custom: false,
        });
        onChange("mock");
      });

    return () => {
      cancelled = true;
    };
  }, [provider, onChange]);

  const selectValue = customMode ? CUSTOM_MODEL_VALUE : value;
  const models = catalog?.models ?? (value ? [{ id: value, label: value }] : []);

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

  return (
    <div className={cn("flex min-w-0 items-center gap-2", className)}>
      <Select
        value={selectValue}
        onValueChange={handleSelectChange}
        disabled={disabled || !catalog}
      >
        <SelectTrigger
          size="sm"
          className="h-7 max-w-[220px] gap-1.5 border-border/70 bg-background/80 text-xs"
        >
          <Cpu className="size-3.5 shrink-0 text-muted-foreground" />
          <SelectValue placeholder="Model" />
        </SelectTrigger>
        <SelectContent>
          {models.map((model) => (
            <SelectItem key={model.id} value={model.id} className="text-xs">
              {model.label}
            </SelectItem>
          ))}
          {catalog?.supports_custom ? (
            <SelectItem value={CUSTOM_MODEL_VALUE} className="text-xs">
              Custom model…
            </SelectItem>
          ) : null}
        </SelectContent>
      </Select>

      {customMode && catalog?.supports_custom ? (
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
