"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AI_PROVIDER_OPTIONS,
  DEFAULT_AI_SETTINGS,
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
};

function isProviderId(value: string): value is AiProviderId {
  return value in PROVIDER_FIELDS;
}

function ProviderFields({
  providerId,
  ai,
  onChange,
  highlighted,
}: {
  providerId: AiProviderId;
  ai: AiSettings;
  onChange: (next: AiSettings) => void;
  highlighted?: boolean;
}) {
  const fields = PROVIDER_FIELDS[providerId];
  const option = AI_PROVIDER_OPTIONS.find((p) => p.id === providerId);

  if (providerId === "mock") {
    return (
      <p className="text-xs text-muted-foreground">
        Mock mode needs no API key. Insights, research, and reflection use
        deterministic offline responses.
      </p>
    );
  }

  const patch = (key: keyof AiSettings, val: string) =>
    onChange({ ...ai, [key]: val });

  return (
    <div
      className={cn(
        "space-y-3 rounded-lg border p-3",
        highlighted ? "border-primary/40 bg-primary/5" : "border-border/60",
      )}
    >
      <div>
        <p className="text-sm font-medium">{option?.label}</p>
        <p className="text-xs text-muted-foreground">{option?.description}</p>
      </div>

      {fields.keyField && (
        <div className="space-y-1.5">
          <Label htmlFor={`${providerId}-key`}>API key</Label>
          <Input
            id={`${providerId}-key`}
            type="password"
            value={
              ai[fields.keyField] === SECRET_MASK ? "" : String(ai[fields.keyField])
            }
            placeholder={
              ai[fields.keyField] === SECRET_MASK
                ? SECRET_MASK
                : fields.keyPlaceholder
            }
            onChange={(e) => patch(fields.keyField!, e.target.value)}
          />
        </div>
      )}

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

      <div className="space-y-1.5">
        <Label htmlFor={`${providerId}-model`}>Model</Label>
        <Input
          id={`${providerId}-model`}
          value={String(ai[fields.modelField] || "")}
          placeholder={String(DEFAULT_AI_SETTINGS[fields.modelField] || "")}
          onChange={(e) => patch(fields.modelField, e.target.value)}
        />
      </div>
    </div>
  );
}

export function AiProviderSettings({ value, onChange }: Props) {
  const ai = normalizeAiSettings(value);
  const activeId = isProviderId(ai.provider) ? ai.provider : "mock";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="ai-provider">Active provider</Label>
        <select
          id="ai-provider"
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
          value={activeId}
          onChange={(e) =>
            onChange({ ...ai, provider: e.target.value as AiProviderId })
          }
        >
          {AI_PROVIDER_OPTIONS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">
          The active provider is used for insights, research, and reflection.
          Configure keys and endpoints for each provider below.
        </p>
      </div>

      <ProviderFields
        providerId={activeId}
        ai={ai}
        onChange={onChange}
        highlighted
      />

      <details className="group rounded-lg border border-border/60">
        <summary className="cursor-pointer list-none px-3 py-2 text-sm font-medium marker:content-none [&::-webkit-details-marker]:hidden">
          All provider endpoints
        </summary>
        <div className="space-y-3 border-t border-border/60 p-3">
          {AI_PROVIDER_OPTIONS.filter((p) => p.id !== "mock").map((p) => (
            <ProviderFields
              key={p.id}
              providerId={p.id}
              ai={ai}
              onChange={onChange}
              highlighted={p.id === activeId}
            />
          ))}
        </div>
      </details>
    </div>
  );
}
