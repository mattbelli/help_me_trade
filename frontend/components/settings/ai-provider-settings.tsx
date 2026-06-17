"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DefaultModelSelect } from "@/components/settings/default-model-select";
import { ChatModelPickerSettings } from "@/components/settings/chat-model-picker-settings";
import {
  AI_PROVIDER_OPTIONS,
  DEFAULT_AI_SETTINGS,
  getDefaultLlm,
  normalizeAiSettings,
  PROVIDER_FIELDS,
  type AiProviderId,
  type AiSettings,
} from "@/lib/ai-settings";
import { SECRET_MASK } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  value: AiSettings;
  onChange: (next: AiSettings) => void;
  modelsRevision?: number;
};

function ProviderAccordion({
  providerId,
  ai,
  onChange,
  defaultOpen,
  modelsRevision,
}: {
  providerId: AiProviderId;
  ai: AiSettings;
  onChange: (next: AiSettings) => void;
  defaultOpen?: boolean;
  modelsRevision?: number;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  const fields = PROVIDER_FIELDS[providerId];
  const option = AI_PROVIDER_OPTIONS.find((p) => p.id === providerId);

  const patch = (key: keyof AiSettings, val: string) =>
    onChange({ ...ai, [key]: val });

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="rounded-lg border border-border/60"
    >
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-sm font-medium hover:bg-muted/40">
        <span>{option?.label}</span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-3 border-t border-border/60 p-3">
        {option?.description ? (
          <p className="text-muted-foreground text-xs">{option.description}</p>
        ) : null}

        {fields.keyField ? (
          <div className="space-y-1.5">
            <Label htmlFor={`${providerId}-key`}>API key</Label>
            <Input
              id={`${providerId}-key`}
              type="password"
              value={
                ai[fields.keyField] === SECRET_MASK
                  ? ""
                  : String(ai[fields.keyField])
              }
              placeholder={
                ai[fields.keyField] === SECRET_MASK
                  ? SECRET_MASK
                  : fields.keyPlaceholder
              }
              onChange={(e) => patch(fields.keyField!, e.target.value)}
            />
          </div>
        ) : null}

        <div className="space-y-1.5">
          <Label htmlFor={`${providerId}-base`}>API base URL</Label>
          <Input
            id={`${providerId}-base`}
            value={String(ai[fields.baseUrlField] || "")}
            placeholder={String(
              DEFAULT_AI_SETTINGS[fields.baseUrlField] || "",
            )}
            onChange={(e) => patch(fields.baseUrlField, e.target.value)}
          />
        </div>

        <ChatModelPickerSettings
          providerId={providerId}
          ai={ai}
          onChange={onChange}
          className="border-0 p-0"
          modelsRevision={modelsRevision}
        />
      </CollapsibleContent>
    </Collapsible>
  );
}

export function AiProviderSettings({ value, onChange, modelsRevision = 0 }: Props) {
  const ai = normalizeAiSettings(value);
  const defaultLlm = getDefaultLlm(ai);

  return (
    <div className="space-y-4">
      <DefaultModelSelect
        ai={ai}
        onChange={onChange}
        modelsRevision={modelsRevision}
      />

      <div className="space-y-2">
        <p className="text-sm font-medium">Providers</p>
        <p className="text-muted-foreground text-xs">
          Add API keys and endpoints for any provider. Pick which model to use
          above.
        </p>
        <div className="space-y-2">
          {AI_PROVIDER_OPTIONS.map((p) => (
            <ProviderAccordion
              key={p.id}
              providerId={p.id}
              ai={ai}
              onChange={onChange}
              defaultOpen={p.id === defaultLlm.provider}
              modelsRevision={modelsRevision}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
